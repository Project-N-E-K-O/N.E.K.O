const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return payload; },
    clone() { return response(status, payload); },
  };
}

async function main() {
  const calls = [];
  const listeners = new Map();
  const intervals = new Map();
  const intervalDelays = new Map();
  const timeouts = new Map();
  const timeoutDelays = new Map();
  const clearedIntervals = [];
  const clearedTimeouts = [];
  const pendingFetches = [];
  const storage = new Map();
  let nextTimerId = 1;
  let realtimeAttempts = 0;
  let refreshCount = 0;
  let playbackChannel = null;
  let playbackChannelClosed = false;
  let voiceControlChannel = null;
  let voiceControlChannelClosed = false;
  const voiceControlMessages = [];
  const speechRecognitions = [];

  const addEventListener = (type, handler) => {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(handler);
  };
  const removeEventListener = (type, handler) => listeners.get(type)?.delete(handler);
  const originalWarn = () => {};
  const originalError = () => {};
  const consoleMock = { log() {}, warn: originalWarn, error: originalError };
  const documentMock = { addEventListener, removeEventListener };
  class BroadcastChannelMock {
    constructor(name) {
      this.name = name;
      this.onmessage = null;
      if (name === 'playback-channel') playbackChannel = this;
      if (name === 'voice-control-channel') voiceControlChannel = this;
    }
    postMessage(message) {
      if (this === voiceControlChannel) voiceControlMessages.push(message);
    }
    close() {
      if (this === playbackChannel) playbackChannelClosed = true;
      if (this === voiceControlChannel) voiceControlChannelClosed = true;
    }
  }
  class SpeechRecognitionMock {
    constructor() {
      this.startCount = 0;
      this.stopCount = 0;
      this.abortCount = 0;
      speechRecognitions.push(this);
    }
    start() { this.startCount += 1; }
    stop() { this.stopCount += 1; }
    abort() { this.abortCount += 1; }
  }
  const security = {
    peekCachedToken: () => 'cached-token',
    getMutationHeaders: async () => ({ 'X-CSRF-Token': 'fresh-token' }),
    refreshToken: async () => { refreshCount += 1; },
  };
  const windowMock = {
    console: consoleMock,
    document: documentMock,
    navigator: { sendBeacon: () => false },
    location: { origin: 'http://127.0.0.1:48911' },
    lanlan_config: { lanlan_name: 'runtime-neko' },
    nekoLocalMutationSecurity: security,
    localStorage: {
      get length() { return storage.size; },
      key(index) { return Array.from(storage.keys())[index] ?? null; },
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
    BroadcastChannel: BroadcastChannelMock,
    SpeechRecognition: SpeechRecognitionMock,
    addEventListener,
    removeEventListener,
    setInterval(callback, delay) {
      const id = nextTimerId++;
      intervals.set(id, callback);
      intervalDelays.set(id, delay);
      return id;
    },
    clearInterval(id) {
      intervals.delete(id);
      intervalDelays.delete(id);
      clearedIntervals.push(id);
    },
    setTimeout(callback, delay) {
      const id = nextTimerId++;
      timeouts.set(id, callback);
      timeoutDelays.set(id, delay);
      return id;
    },
    clearTimeout(id) {
      timeouts.delete(id);
      timeoutDelays.delete(id);
      clearedTimeouts.push(id);
    },
    async fetch(url, options = {}) {
      calls.push({ url, options });
      const requestBody = typeof options.body === 'string'
        ? JSON.parse(options.body)
        : {};
      if (
        requestBody.runtime_test_mode === 'pending'
        || (url === '/api/game/logs' && requestBody.details?.runtime_test_mode === 'pending')
      ) {
        return new Promise((resolve, reject) => {
          const pending = { url, options, resolve, reject, aborted: false };
          pendingFetches.push(pending);
          const rejectAsAborted = () => {
            pending.aborted = true;
            const error = new Error('aborted');
            error.name = 'AbortError';
            reject(error);
          };
          if (options.signal?.aborted) rejectAsAborted();
          else options.signal?.addEventListener('abort', rejectAsAborted, { once: true });
        });
      }
      if (url.endsWith('/realtime-context')) {
        realtimeAttempts += 1;
        return realtimeAttempts === 1
          ? response(403, { error_code: 'csrf_validation_failed' })
          : response(200, { ok: true });
      }
      if (url.endsWith('/speak')) {
        return response(200, { ok: true, speech_id: 'adapter-speech-1', audio_sent: true });
      }
      if (url.includes('/api/config/page_config')) {
        return response(200, { ok: true, autostart_csrf_token: 'page-config-token' });
      }
      if (url.includes('/api/game/soccer/character')) {
        return response(200, { ok: true, lanlan_name: 'resolved-runtime-neko' });
      }
      if (url.endsWith('/route/heartbeat')) return response(200, { ok: true, active: true });
      return response(200, { ok: true });
    },
  };

  global.window = windowMock;
  global.document = documentMock;
  global.navigator = windowMock.navigator;
  if (typeof global.Blob === 'undefined') {
    global.Blob = class Blob {
      constructor(parts, options) {
        this.parts = parts;
        this.type = options?.type || '';
      }
    };
  }

  const hostPath = path.resolve(__dirname, '../../static/game/sdk/neko-minigame-same-origin-host.js');
  const launchNode = {
    textContent: JSON.stringify({
      registrations: {
        soccer: {
          mode: 'registered',
          gameId: 'soccer',
          publisherId: 'project-n-e-k-o',
          version: '1.0.0',
          allowedCapabilities: [
            'runtime', 'logging', 'audio', 'speech-output', 'dialogue', 'quick-lines',
            'voice-input', 'avatar-renderer', 'storage',
          ],
        },
        'example-game': {
          mode: 'development',
          gameId: 'example-game',
          publisherId: 'test-host',
          version: '1.0.0',
          allowedCapabilities: ['runtime', 'logging', 'dialogue', 'storage'],
        },
      },
    }),
    remove() { this.removed = true; },
  };
  documentMock.currentScript = null;
  documentMock.getElementById = (id) => id === 'neko-minigame-host-launch' ? launchNode : null;
  documentMock.createElement = () => ({ remove() { this.removed = true; } });
  documentMock.head = {
    appendChild(script) {
      documentMock.currentScript = script;
      try {
        vm.runInThisContext(fs.readFileSync(hostPath, 'utf8'), { filename: hostPath });
      } finally {
        documentMock.currentScript = null;
      }
      script.onload?.();
    },
  };
  const registrationPath = path.resolve(
    __dirname,
    '../../static/game/games/soccer/soccer-neko-host-registration.js',
  );
  vm.runInThisContext(fs.readFileSync(registrationPath, 'utf8'), { filename: registrationPath });
  const bootstrapPath = path.resolve(
    __dirname,
    '../../static/game/sdk/neko-minigame-same-origin-bootstrap.js',
  );
  vm.runInThisContext(fs.readFileSync(bootstrapPath, 'utf8'), { filename: bootstrapPath });
  await window.nekoMiniGameSameOriginHostReady;
  assert(launchNode.removed === true, 'soccer launch registration was not consumed');
  const adapterPath = path.resolve(__dirname, '../../static/game/games/soccer/soccer-neko-adapter.js');
  vm.runInThisContext(fs.readFileSync(adapterPath, 'utf8'), { filename: adapterPath });
  const connectSoccerHost = (host) => host.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'soccer',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      optionalCapabilities: ['dialogue', 'quick-lines', 'voice-input', 'speech-output', 'storage'],
    },
  });

  const genericAdapter = window.createNekoMiniGameSameOriginHost({
    gameType: 'example-game',
    source: 'example-game-test',
    displayName: 'Example Game',
    sessionId: 'example-game-test-session',
  });
  const genericHandshake = genericAdapter.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'example-game',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      optionalCapabilities: ['dialogue', 'storage'],
    },
  });
  assert(genericHandshake.accepted, 'generic host rejected the neutral fixture identity');
  assert(genericHandshake.registration.gameId === 'example-game',
    'generic host did not preserve the configured game identity');
  assert(genericAdapter.sessionId === 'example-game-test-session',
    'generic host did not preserve the launch session');
  assert(genericHandshake.grantedCapabilities.includes('storage'),
    'generic host did not grant bounded local game storage');
  genericAdapter.requestGameStorage('set', { key: 'leaderboards/main', value: { score: 3 } });
  const storedLeaderboard = genericAdapter.requestGameStorage('get', { key: 'leaderboards/main' });
  assert(storedLeaderboard.found && storedLeaderboard.value.score === 3,
    'generic host local game storage did not round-trip JSON');
  let storageQuotaError = null;
  try {
    genericAdapter.requestGameStorage('set', {
      key: 'leaderboards/oversized',
      value: { text: 'x'.repeat(70 * 1024) },
    });
  } catch (error) { storageQuotaError = error; }
  assert(storageQuotaError?.code === 'quota_exceeded',
    'generic host local game storage did not enforce its value limit');
  genericAdapter.dispose();

  let avatarHostDisposed = 0;
  let avatarMountConfig = null;
  const avatarAdapter = await window.createSoccerNekoAdapter({
    avatarHost: {
      async mount(config) {
        avatarMountConfig = config;
        return { dispose() {} };
      },
      dispose() { avatarHostDisposed += 1; },
    },
  });
  avatarAdapter.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'soccer',
      version: '1.0.0',
      requiredCapabilities: ['logging'],
      optionalCapabilities: ['avatar-renderer', 'voice-input', 'dialogue', 'quick-lines'],
    },
  });
  const avatarController = await avatarAdapter.mountAvatar({
    slot: 'ai',
    viewport: { mode: 'fixed', width: 200, height: 300 },
  });
  assert(typeof avatarController.dispose === 'function', 'avatar controller was not forwarded');
  assert(avatarMountConfig?.viewport?.mode === 'fixed', 'avatar config was not forwarded');
  avatarAdapter.dispose();
  assert(avatarHostDisposed === 1, 'avatar host was not released by adapter disposal');
  avatarAdapter.dispose();
  assert(avatarHostDisposed === 1, 'repeated adapter disposal released the avatar host twice');

  let audioHostDisposed = 0;
  let audioMountConfig = null;
  const audioAdapter = await window.createSoccerNekoAdapter({
    audioHost: {
      mount(config) {
        audioMountConfig = config;
        return { dispose() {} };
      },
      dispose() { audioHostDisposed += 1; },
    },
  });
  audioAdapter.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'soccer',
      version: '1.0.0',
      requiredCapabilities: ['logging'],
      optionalCapabilities: ['audio'],
    },
  });
  const audioController = audioAdapter.mountAudio({ slot: 'main', resources: { sfx: {} } });
  assert(typeof audioController.dispose === 'function', 'audio controller was not forwarded');
  assert(audioMountConfig?.slot === 'main', 'audio config was not forwarded');
  audioAdapter.dispose();
  assert(audioHostDisposed === 1, 'audio host was not released by adapter disposal');

  const adapter = await window.createSoccerNekoAdapter({
    logQueueLimit: 8,
    logConcurrency: 2,
    logPumpIntervalMs: 1,
    avatarHost: {
      async mount() { return { dispose() {} }; },
      dispose() {},
    },
  });
  const handshake = adapter.connectGame({
    sdkVersion: '0.1.0',
    protocolVersions: ['1'],
    manifest: {
      id: 'soccer',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging', 'speech-output'],
      optionalCapabilities: ['avatar-renderer', 'voice-input', 'dialogue', 'quick-lines'],
    },
  });
  assert(handshake.accepted === true, 'registered soccer handshake was rejected');
  assert(handshake.registration?.mode === 'registered', 'soccer registration mode was not returned');
  assert(handshake.registration?.gameId === 'soccer', 'soccer registration identity was not returned');
  assert(handshake.grantedCapabilities.includes('runtime'), 'runtime was not granted by soccer host');
  assert(handshake.grantedCapabilities.includes('avatar-renderer'),
    'available avatar host was not granted by soccer handshake');
  assert(handshake.grantedCapabilities.includes('quick-lines'),
    'host-owned soccer quick-lines provider was not granted');
  const unknownHandshake = adapter.connectGame({
    protocolVersions: ['1'],
    manifest: { id: 'unknown-game', version: '1.0.0' },
  });
  assert(unknownHandshake.code === 'game_unregistered',
    'unknown game identity was not rejected by the trusted host');
  const characterResponse = await adapter.getCharacter();
  assert(characterResponse.ok, 'character bootstrap request failed');
  assert(adapter.getRuntimeState().characterName === 'resolved-runtime-neko',
    'resolved character identity was not synchronized into the SDK runtime state');
  const quickLinesResponse = await adapter.getQuickLines({ i18n_language: 'zh-CN' });
  assert(quickLinesResponse.ok, 'host-owned soccer quick-lines provider failed');
  assert(calls.some((call) => call.url === '/api/game/soccer/quick-lines'),
    'soccer quick-lines did not use its host-owned provider');
  const passiveGuardResponse = await adapter.evaluatePassiveGuard({ stage: 1 });
  assert(passiveGuardResponse.ok, 'soccer compatibility PassiveGuard request failed');
  assert(calls.some((call) => call.url.endsWith('/api/game/soccer/passive-guard')),
    'soccer compatibility adapter did not own its PassiveGuard route');
  const originalGetMutationHeaders = security.getMutationHeaders;
  security.getMutationHeaders = async () => ({});
  const fallbackMutationHeaders = await adapter.getMutationHeaders();
  assert(fallbackMutationHeaders['X-CSRF-Token'] === 'page-config-token',
    'empty shared mutation headers did not fall back to page config credentials');
  security.getMutationHeaders = originalGetMutationHeaders;
  adapter.resetSession({ newSession: true });
  adapter.applyRouteState({ lanlan_name: 'runtime-neko' });
  adapter.configureLogger({
    summaryIntervalMs: 1000,
    recoveryQuietMs: 1000,
  });
  assert(listeners.get('error')?.size === 1, 'window error listener was not installed');
  assert(listeners.get('unhandledrejection')?.size === 1, 'rejection listener was not installed');

  const enableResult = await adapter.logger.enable('runtime-test');
  assert(enableResult.ok, 'logger did not enable');
  adapter.logger.info('runtime', 'normal-1', 'first', { long: 'x'.repeat(1400) });
  adapter.logger.info('runtime', 'normal-2', 'second', { preserved: 'y'.repeat(1400) }, false, { preserveDetails: true });
  adapter.logger.info('runtime', 'normal-3', 'limited');
  adapter.logger.info('passive_guard', 'passive_guard_1', 'passive first');
  adapter.logger.info('passive_guard', 'passive_guard_2', 'passive limited');
  adapter.logger.warn('runtime', 'repeating-warning', 'same warning', { code: 'repeat' });
  adapter.logger.warn('runtime', 'repeating-warning', 'same warning', { code: 'repeat' });
  adapter.logger.warn('runtime', 'repeating-warning', 'same warning', { code: 'repeat' });

  const originalDateNow = Date.now;
  const baseNow = originalDateNow();
  Date.now = () => baseNow + 2000;
  const loggerMaintenance = Array.from(intervals.entries())
    .find(([id]) => intervalDelays.get(id) === 1000)?.[1];
  assert(typeof loggerMaintenance === 'function', 'logger maintenance timer was not registered');
  loggerMaintenance();
  Date.now = originalDateNow;
  await adapter.logger.flush();

  const logCalls = calls.filter((call) => call.url === '/api/game/logs');
  assert(logCalls.length === 8, `expected 8 observable log requests, got ${logCalls.length}`);
  const firstLog = JSON.parse(logCalls[0].options.body);
  const secondLog = JSON.parse(logCalls[1].options.body);
  assert(firstLog.session_id === adapter.sessionId, 'logger session context was not attached');
  assert(firstLog.game_type === 'soccer', 'logger game type was not attached');
  assert(firstLog.lanlan_name === 'runtime-neko', 'logger route character context was not attached');
  assert(firstLog.details.long.endsWith('...<truncated>'), 'ordinary details were not truncated');
  assert(secondLog.details.preserved.length === 1400, 'preserveDetails was not respected');
  assert(firstLog._csrf_token === 'cached-token', 'cached CSRF token was not embedded in log payload');
  const repeatedSummary = logCalls
    .map((call) => JSON.parse(call.options.body))
    .find((payload) => payload.event === 'repeated_log_summary');
  const repeatedRecovery = logCalls
    .map((call) => JSON.parse(call.options.body))
    .find((payload) => payload.event === 'repeated_log_recovered');
  assert(repeatedSummary?.details?.total_count === 3, 'repeated warning count was not summarized');
  assert(repeatedRecovery?.details?.total_count === 3, 'repeated warning recovery was not recorded');

  const realtimeResult = await adapter.sendRealtimeContextWithCsrf({ source: 'runtime-test' });
  assert(realtimeResult.ok, 'realtime CSRF retry did not recover');
  assert(realtimeAttempts === 2, `expected one realtime retry, got ${realtimeAttempts} attempts`);
  assert(refreshCount === 1, `expected one CSRF refresh, got ${refreshCount}`);

  const playbackStates = [];
  storage.set('playback-state', JSON.stringify({
    type: 'speech_playback_state',
    active: true,
    speech_id: 'stored-speech',
    remaining_seconds: 1,
    updated_at: Date.now(),
  }));
  adapter.startSpeechOutputBridge({
    storageKey: 'playback-state',
    channelName: 'playback-channel',
    eventName: 'playback-event',
    onState: (state, source) => playbackStates.push({ state, source }),
  });
  playbackChannel.onmessage({ data: { type: 'speech_playback_state', active: true } });
  listeners.get('storage').values().next().value({
    key: 'playback-state',
    newValue: JSON.stringify({ type: 'speech_playback_state', active: false }),
  });
  listeners.get('playback-event').values().next().value({
    detail: { type: 'speech_playback_state', active: true },
  });
  assert(playbackStates.length === 4, 'speech output bridge did not forward all supported sources');
  assert(playbackStates.map((item) => item.source).join(',') === (
    'local_storage_initial,broadcast_channel,local_storage,window_event'
  ),
    'speech playback bridge source labels changed');
  const speechResponse = await adapter.requestSpeechOutput({
    line: 'adapter speech output',
    session_id: adapter.sessionId,
  });
  assert(speechResponse.ok, 'official speech output request did not use the soccer adapter');
  const speechCall = calls.find((call) => call.url.endsWith('/speak'));
  assert(speechCall, 'speech output endpoint was not requested');
  assert(JSON.parse(speechCall.options.body).line === 'adapter speech output',
    'speech output payload changed before reaching the trusted host endpoint');
  const preloadResponse = await adapter.preloadSpeechOutput({
    lines: ['adapter preload line'],
    session_id: adapter.sessionId,
  });
  assert(preloadResponse.ok, 'speech preload did not use the soccer adapter');
  const preloadCall = calls.find((call) => call.url.endsWith('/speech/preload'));
  assert(preloadCall, 'speech preload endpoint was not requested');
  assert(JSON.parse(preloadCall.options.body).lines[0] === 'adapter preload line',
    'speech preload payload changed before reaching the trusted host endpoint');

  const voiceControlStates = [];
  const hostVoiceTranscripts = [];
  adapter.startVoiceControlBridge({
    channelName: 'voice-control-channel',
    onState: (state, source) => voiceControlStates.push({ state, source }),
    onTranscript: (event, source) => hostVoiceTranscripts.push({ event, source }),
  });
  const voiceToggle = adapter.requestVoiceControl('toggle');
  assert(voiceControlMessages.length === 1, 'voice control request was not posted');
  const voiceRequest = voiceControlMessages[0];
  assert(voiceRequest.game_type === 'soccer', 'voice control game type was not attached');
  assert(voiceRequest.session_id === adapter.sessionId, 'voice control session id was not attached');
  voiceControlChannel.onmessage({ data: {
    type: 'game_voice_control_state',
    request_id: voiceRequest.request_id,
    game_type: 'soccer',
    session_id: adapter.sessionId,
    available: true,
    active: true,
    reason: 'started',
  } });
  const voiceToggleState = await voiceToggle;
  assert(voiceToggleState.active === true, 'voice control acknowledgement did not resolve request');
  assert(voiceControlStates.length === 1 && voiceControlStates[0].source === 'broadcast_channel',
    'voice control state source changed');
  voiceControlChannel.onmessage({ data: {
    type: 'game_voice_transcript',
    request_id: 'voice-final-1',
    game_type: 'soccer',
    session_id: adapter.sessionId,
    source: 'voice',
    text: '  final host words  ',
  } });
  assert(hostVoiceTranscripts.length === 1, 'host transcript was not forwarded by the adapter');
  assert(hostVoiceTranscripts[0].event.text === 'final host words', 'host transcript was not normalized');
  assert(hostVoiceTranscripts[0].source === 'broadcast_channel', 'host transcript source changed');
  voiceControlChannel.onmessage({ data: {
    type: 'game_voice_transcript',
    game_type: 'soccer',
    session_id: 'stale-session',
    text: 'stale words',
  } });
  assert(hostVoiceTranscripts.length === 1, 'stale-session transcript reached the active game');
  const cancelledVoiceRequest = adapter.requestVoiceControl('start').then(() => null, (error) => error);
  adapter.resetSession({ newSession: true });
  adapter.applyRouteState({ lanlan_name: 'runtime-neko' });
  const cancelledVoiceError = await cancelledVoiceRequest;
  assert(cancelledVoiceError?.code === 'cancelled', 'new session did not release the old voice control request');

  const transcripts = [];
  adapter.startSpeechRecognition('route-voice', {
    lang: 'zh-CN',
    autoRestart: true,
    restartDelayMs: 10,
    onTranscript: (transcript) => transcripts.push(transcript),
  });
  const routeRecognition = speechRecognitions[0];
  routeRecognition.onstart({});
  const finalResult = [{ transcript: '  runtime speech  ' }];
  finalResult.isFinal = true;
  routeRecognition.onresult({ resultIndex: 0, results: [finalResult] });
  assert(transcripts.join(',') === 'runtime speech', 'managed recognition did not emit final transcript');
  routeRecognition.onend({});
  const restartTimer = Array.from(timeouts.values()).at(-1);
  assert(typeof restartTimer === 'function', 'recognition restart timer was not registered');
  restartTimer();
  assert(routeRecognition.startCount === 2, 'managed recognition did not restart');

  const timeoutRequest = adapter.requestDialogue(
    { runtime_test_mode: 'pending' },
    { timeoutMs: 5 },
  ).then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  const requestTimeout = Array.from(timeouts.values()).at(-1);
  assert(typeof requestTimeout === 'function', 'request timeout was not registered');
  requestTimeout();
  const timeoutError = await timeoutRequest;
  assert(timeoutError?.name === 'NekoMiniGameHostError', 'timeout did not use the host error type');
  assert(timeoutError instanceof window.SoccerNekoHostError, 'soccer error alias was not preserved');
  assert(timeoutError?.code === 'timeout', `expected timeout error, got ${timeoutError?.code}`);

  const externalController = new AbortController();
  const cancelledRequest = adapter.requestDialogue(
    { runtime_test_mode: 'pending' },
    { signal: externalController.signal },
  ).then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  externalController.abort();
  const cancelledError = await cancelledRequest;
  assert(cancelledError?.code === 'cancelled', `expected cancelled error, got ${cancelledError?.code}`);

  const endController = new AbortController();
  const cancelledEnd = adapter.end(
    { runtime_test_mode: 'pending', reason: 'cancel-test' },
    { signal: endController.signal },
  ).then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  const pendingCancelledEnd = pendingFetches
    .filter((item) => item.url.endsWith('/end') && !item.aborted)
    .at(-1);
  assert(pendingCancelledEnd, 'route-end cancellation request was not captured');
  endController.abort();
  const cancelledEndError = await cancelledEnd;
  assert(cancelledEndError?.code === 'cancelled',
    `expected cancelled route-end error, got ${cancelledEndError?.code}`);
  assert(pendingCancelledEnd.aborted, 'route-end AbortSignal was not forwarded to the request');

  const limitedAdapter = await window.createSoccerNekoAdapter({ pendingRequestLimit: 1 });
  connectSoccerHost(limitedAdapter);
  const limitedPending = limitedAdapter.requestDialogue({ runtime_test_mode: 'pending' })
    .then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  const busyError = await limitedAdapter.requestDialogue({ runtime_test_mode: 'pending' })
    .then(() => null, (error) => error);
  assert(busyError?.code === 'busy', `expected busy error at pending limit, got ${busyError?.code}`);
  limitedAdapter.dispose();
  const limitedDisposedError = await limitedPending;
  assert(limitedDisposedError?.code === 'disposed', 'limited adapter did not release its pending request');

  const isolatedAdapter = await window.createSoccerNekoAdapter({
    pendingRequestLimit: 1,
    logQueueLimit: 2,
    logConcurrency: 1,
    logPumpIntervalMs: 1,
  });
  connectSoccerHost(isolatedAdapter);
  isolatedAdapter.configureLogger();
  const isolatedEnable = await isolatedAdapter.logger.enable('isolated-log-transport');
  assert(isolatedEnable.ok, 'isolated logger did not enable');
  isolatedAdapter.logger.info('runtime', 'slow-log', 'slow log', { runtime_test_mode: 'pending' });
  const isolatedPump = Array.from(timeouts.entries())
    .find(([id]) => timeoutDelays.get(id) === 1)?.[1];
  assert(typeof isolatedPump === 'function', 'isolated log pump was not scheduled');
  isolatedPump();
  await new Promise((resolve) => setImmediate(resolve));
  const coreWhileLogPending = await isolatedAdapter.requestDialogue({ source: 'core-while-log-pending' });
  assert(coreWhileLogPending.ok, 'pending diagnostic log blocked a core dialogue request');
  isolatedAdapter.logger.info('runtime', 'queued-1', 'queued log 1');
  isolatedAdapter.logger.info('runtime', 'queued-2', 'queued log 2');
  isolatedAdapter.logger.info('runtime', 'overflowed', 'must remain observable');
  const slowLogRequest = pendingFetches.find((item) => item.url === '/api/game/logs' && !item.aborted);
  assert(slowLogRequest, 'slow diagnostic log request was not captured');
  slowLogRequest.resolve(response(200, { ok: true }));
  await isolatedAdapter.logger.flush();
  const isolatedLogPayloads = calls
    .filter((call) => call.url === '/api/game/logs')
    .map((call) => JSON.parse(call.options.body));
  const overflowSummary = isolatedLogPayloads.find((payload) => payload.event === 'log_queue_overflow');
  assert(overflowSummary?.details?.dropped_count >= 1, 'log queue overflow was silently discarded');
  isolatedAdapter.dispose();

  const logDisposeAdapter = await window.createSoccerNekoAdapter({
    logQueueLimit: 2,
    logConcurrency: 1,
    logPumpIntervalMs: 3,
  });
  connectSoccerHost(logDisposeAdapter);
  const pendingLogResult = logDisposeAdapter.postLog({
    session_id: logDisposeAdapter.sessionId,
    game_type: 'soccer',
    source: 'runtime-test',
    level: 'info',
    category: 'runtime',
    event: 'dispose-pending-log',
    message: 'pending log must be released',
    details: { runtime_test_mode: 'pending' },
  });
  const logDisposePump = Array.from(timeouts.entries())
    .find(([id]) => timeoutDelays.get(id) === 3)?.[1];
  assert(typeof logDisposePump === 'function', 'log disposal test pump was not scheduled');
  logDisposePump();
  await new Promise((resolve) => setImmediate(resolve));
  const pendingDisposedLog = pendingFetches
    .filter((item) => item.url === '/api/game/logs' && !item.aborted)
    .at(-1);
  assert(pendingDisposedLog, 'pending log disposal request was not captured');
  logDisposeAdapter.dispose();
  const disposedLogResult = await pendingLogResult;
  await new Promise((resolve) => setImmediate(resolve));
  assert(disposedLogResult?.reason === 'disposed', 'disposed log request did not resolve as disposed');
  assert(pendingDisposedLog.aborted, 'disposed log request was not aborted');

  const heartbeatAdapter = await window.createSoccerNekoAdapter();
  connectSoccerHost(heartbeatAdapter);
  const heartbeatFailurePromise = heartbeatAdapter.heartbeat(
    { runtime_test_mode: 'pending' },
    {
      timeoutMs: 7,
    },
  ).then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  const heartbeatTimeout = Array.from(timeouts.entries())
    .filter(([id]) => timeoutDelays.get(id) === 7)
    .at(-1)?.[1];
  assert(typeof heartbeatTimeout === 'function', 'heartbeat request timeout was not registered');
  heartbeatTimeout();
  await new Promise((resolve) => setImmediate(resolve));
  const heartbeatFailure = await heartbeatFailurePromise;
  assert(heartbeatFailure?.code === 'timeout', 'heartbeat timeout was misclassified');
  heartbeatAdapter.dispose();

  const unloadAdapter = await window.createSoccerNekoAdapter();
  connectSoccerHost(unloadAdapter);
  const unloadEnd = unloadAdapter.end(
    { runtime_test_mode: 'pending', reason: 'pagehide' },
    { useBeacon: true },
  );
  await new Promise((resolve) => setImmediate(resolve));
  const pendingUnloadEnd = pendingFetches
    .filter((item) => item.url.endsWith('/end'))
    .at(-1);
  assert(pendingUnloadEnd, 'unload route-end fallback was not started');
  unloadAdapter.dispose({ preservePendingOperations: ['route_end'] });
  await new Promise((resolve) => setImmediate(resolve));
  assert(!pendingUnloadEnd.aborted, 'dispose aborted the unload route-end fallback');
  pendingUnloadEnd.resolve(response(200, { ok: true }));
  const unloadEndResult = await unloadEnd;
  assert(unloadEndResult.ok, 'preserved unload route-end fallback did not complete');

  const disposedRequest = adapter.requestDialogue({ runtime_test_mode: 'pending' })
    .then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  adapter.dispose();
  const disposedError = await disposedRequest;
  assert(disposedError?.code === 'disposed', `expected disposed error, got ${disposedError?.code}`);
  assert(!listeners.get('error')?.size, 'window error listener was not removed');
  assert(!listeners.get('unhandledrejection')?.size, 'rejection listener was not removed');
  assert(!listeners.get('storage')?.size, 'storage listener was not removed');
  assert(!listeners.get('playback-event')?.size, 'speech playback window listener was not removed');
  assert(playbackChannelClosed, 'speech playback BroadcastChannel was not closed');
  assert(playbackChannel.onmessage === null, 'speech playback channel handler was not released');
  assert(voiceControlChannelClosed, 'voice control BroadcastChannel was not closed');
  assert(voiceControlChannel.onmessage === null, 'voice control channel handler was not released');
  assert(routeRecognition.abortCount >= 1, 'managed recognition was not aborted during dispose');
  assert(routeRecognition.onresult === null, 'managed recognition handlers were not released');
  assert(clearedTimeouts.length >= 2, 'logger and heartbeat timeouts were not cleared');
  assert(consoleMock.warn === originalWarn, 'console.warn was not restored');
  assert(consoleMock.error === originalError, 'console.error was not restored');

  process.stdout.write(`soccer host adapter runtime test passed (${calls.length} requests)\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
