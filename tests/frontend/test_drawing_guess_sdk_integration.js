const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {status, headers:{'content-type':'application/json'}});
}

async function main() {
  const sdkDir = path.resolve(__dirname, '../../static/game/sdk');
  const hostPath = path.join(sdkDir, 'neko-minigame-same-origin-host.js');
  const bootstrapPath = path.join(sdkDir, 'neko-minigame-same-origin-bootstrap.js');
  const sdkPath = path.join(sdkDir, 'neko-minigame-sdk.js');
  const calls = [];
  let trustedAvatarFactoryCalls = 0;
  let trustedAvatarMounts = 0;
  let forgedAvatarMounts = 0;
  let controllerVoiceActive = false;
  const voiceControlRequests = [];
  const localStorageValues = new Map();
  const localStorageMock = {
    get length() { return localStorageValues.size; },
    key(index) { return [...localStorageValues.keys()][index] ?? null; },
    getItem(key) {
      const normalized = String(key);
      return localStorageValues.has(normalized) ? localStorageValues.get(normalized) : null;
    },
    setItem(key, value) {
      localStorageValues.set(String(key), String(value));
    },
    removeItem(key) {
      localStorageValues.delete(String(key));
    },
  };
  const fetchImpl = async (url, init = {}) => {
    const body = init.body ? JSON.parse(init.body) : {};
    calls.push({ url: String(url), body });
    if (String(url).startsWith('/api/config/page_config')) {
      return jsonResponse({ autostart_csrf_token: 'drawing-sdk-token' });
    }
    if (String(url).endsWith('/route/start')) {
      return jsonResponse({
        ok: true,
        state: {
          game_route_active: true,
          session_id: body.session_id,
          lanlan_name: 'SDK Neko',
        },
      });
    }
    if (String(url).endsWith('/round/start')) {
      return jsonResponse({
        ok: true,
        command: 'round:start',
        accepted_marker: body.marker,
      });
    }
    if (String(url).endsWith('/speak')) {
      return jsonResponse({
        ok: true,
        audio_sent: true,
        speech_id: 'drawing-sdk-speech',
      });
    }
    if (String(url).endsWith('/end')) {
      return jsonResponse({
        ok: true,
        closed: true,
        route_closed: true,
        session_id: 'drawing-sdk-session',
      });
    }
    return jsonResponse({ ok: true });
  };

  const launchNode = {
    textContent: JSON.stringify({
      registrations: {
        'drawing-guess': {
          mode: 'development',
          gameId: 'drawing-guess',
          routeGameType: 'drawing_guess',
          publisherId: 'project-neko',
          version: '0.1.0',
          allowedCapabilities: [
            'runtime', 'logging', 'voice-input', 'speech-output', 'avatar-renderer', 'memory',
            'storage',
          ],
          commandRoutes: {
            'round:start': {
              path: 'round/start',
              maxRequestBytes: 65536,
              maxTimeoutMs: 30000,
            },
            'round:ai-draw': {
              path: 'ai-draw',
              maxRequestBytes: 65536,
              maxTimeoutMs: 90000,
            },
            'round:ai-draw-review': {
              path: 'ai-draw/review',
              maxRequestBytes: 2097152,
              maxTimeoutMs: 120000,
            },
            'round:input': {
              path: 'input',
              maxRequestBytes: 65536,
              maxTimeoutMs: 30000,
            },
            'round:feedback': {
              path: 'input',
              maxRequestBytes: 2097152,
              maxTimeoutMs: 350000,
            },
            'round:choose-word': {
              path: 'choose-word',
              maxRequestBytes: 65536,
              maxTimeoutMs: 30000,
            },
            'round:timeout': {
              path: 'timeout',
              maxRequestBytes: 65536,
              maxTimeoutMs: 30000,
            },
            'round:vision-guess': {
              path: 'vision-guess',
              maxRequestBytes: 2097152,
              maxTimeoutMs: 350000,
            },
          },
        },
      },
    }),
    remove() { this.removed = true; },
  };
  Object.defineProperty(launchNode, 'nekoCapabilityProviders', {
    value: {
      'drawing-guess': {
        avatarHostFactory() {
          trustedAvatarFactoryCalls += 1;
          return {
            async getCurrentCharacter() {
              return {
                name: 'SDK Neko',
                model: { type: 'mmd', path: '/models/sdk-neko.pmx' },
                rendererAvailable: true,
                system_prompt: 'must-not-cross-the-boundary',
              };
            },
            async getCharacter(name) {
              return {
                name,
                model: { type: 'pngtuber', path: '/models/sdk-neko.png' },
                rendererAvailable: true,
              };
            },
            async listCharacters() { return ['SDK Neko', 'PNG Neko']; },
            async mount(config) {
              trustedAvatarMounts += 1;
              return {
                async setModel() {},
                setView(view) { calls.push({ url: 'avatar:view', body: view }); },
                setSpeaking(active) { calls.push({ url: 'avatar:speaking', body: { active } }); },
                focus() {},
                setEmotion() {},
                pause() {},
                resume() {},
                getState() { return { ready: true, type: config.model.type }; },
                dispose() {},
              };
            },
            dispose() {},
          };
        },
      },
    },
  });
  const listeners = new Map();
  class CustomEventMock {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  }
  const windowMock = {
    AbortController,
    console: { log() {}, warn() {}, error() {} },
    fetch: fetchImpl,
    location: { origin: 'http://127.0.0.1:48911' },
    i18next: { language: 'zh-CN' },
    navigator: { sendBeacon: () => false },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    CustomEvent: CustomEventMock,
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(handler);
    },
    removeEventListener(type, handler) {
      listeners.get(type)?.delete(handler);
    },
    dispatchEvent(event) {
      for (const handler of listeners.get(event?.type) || []) handler.call(windowMock, event);
      return true;
    },
    localStorage: localStorageMock,
    crypto: {
      getRandomValues(values) {
        values.fill(11);
        return values;
      },
    },
    appState: {
      pendingAudioChunkMetaQueue: [],
    },
    appAudioPlayback: {
      enqueueIncomingAudioBlob() {},
      schedulePendingAudioMetaStallCheck() {},
    },
  };
  windowMock.addEventListener('neko-game-voice-control-message', (event) => {
    const request = event?.detail;
    if (request?.type !== 'game_voice_control_request') return;
    voiceControlRequests.push(request);
    if (request.action === 'start') controllerVoiceActive = true;
    else if (request.action === 'stop') controllerVoiceActive = false;
    else if (request.action === 'toggle') controllerVoiceActive = !controllerVoiceActive;
    windowMock.dispatchEvent(new windowMock.CustomEvent('neko-game-voice-control-message', {
      detail: {
        type: 'game_voice_control_state',
        message_id: `voice-state-${voiceControlRequests.length}`,
        game_type: request.game_type,
        session_id: request.session_id,
        sdk_route_instance_id: request.sdk_route_instance_id,
        request_id: request.request_id,
        route_active: true,
        active: controllerVoiceActive,
        ok: true,
        reason: request.action === 'query' ? 'queried' : `${request.action}ed`,
      },
    }));
  });
  windowMock.document = {
    currentScript: null,
    documentElement: { lang: 'zh-CN' },
    hidden: false,
    visibilityState: 'visible',
    getElementById(id) {
      return id === 'neko-minigame-host-launch' ? launchNode : null;
    },
    createElement() {
      return { remove() { this.removed = true; } };
    },
    head: {
      appendChild(script) {
        windowMock.document.currentScript = script;
        try {
          vm.runInThisContext(fs.readFileSync(hostPath, 'utf8'), { filename: hostPath });
        } finally {
          windowMock.document.currentScript = null;
        }
        script.onload?.();
      },
    },
    addEventListener() {},
    removeEventListener() {},
  };
  global.window = windowMock;

  vm.runInThisContext(fs.readFileSync(bootstrapPath, 'utf8'), { filename: bootstrapPath });
  await windowMock.nekoMiniGameSameOriginHostReady;
  vm.runInThisContext(fs.readFileSync(sdkPath, 'utf8'), { filename: sdkPath });

  const createHost = await windowMock.nekoMiniGameSameOriginHostReady;
  const transport = createHost({
    gameType: 'drawing-guess',
    routeGameType: 'forged-route',
    sessionId: 'drawing-sdk-session',
    source: 'drawing_guess',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
    // Do not use the deprecated legacy override in a new SDK integration.
    trustedAvatarHost: {
      mount() { forgedAvatarMounts += 1; },
      getCharacter() { return null; },
      listCharacters() { return []; },
    },
  });
  const game = await windowMock.NekoMiniGame.connect({
    id: 'drawing-guess',
    version: '0.1.0',
    protocolVersion: '1',
    requiredCapabilities: [
      'runtime', 'logging', 'speech-output', 'avatar-renderer', 'memory',
    ],
    optionalCapabilities: ['voice-input', 'storage'],
    contracts: {
      commands: {
        'round:start': {
          request: {
            type: 'object',
            additionalProperties: true,
          },
          response: {
            type: 'object',
            properties: {
              ok: { type: 'boolean' },
              command: { type: 'string' },
              accepted_marker: { type: 'string' },
            },
            required: ['ok', 'command', 'accepted_marker'],
            additionalProperties: true,
          },
        },
        'round:ai-draw-review': {
          request: {
            type: 'object',
            properties: {
              client_round_token: { type: 'integer', minimum: 0 },
              image_data_url: { type: 'string', maxLength: 1800000 },
            },
            required: ['client_round_token', 'image_data_url'],
            additionalProperties: false,
          },
          response: {
            type: 'object',
            properties: { ok: { type: 'boolean' } },
            required: ['ok'],
            additionalProperties: true,
          },
        },
      },
    },
  }, { transport, windowImpl: windowMock, documentImpl: windowMock.document });

  assert(game.capabilities.granted.join(',')
    === 'runtime,logging,speech-output,avatar-renderer,memory,voice-input,storage',
    `unexpected drawing capability grant: ${game.capabilities.granted.join(',')}`);
  assert(trustedAvatarFactoryCalls === 1 && forgedAvatarMounts === 0,
    'the game replaced the bootstrap-owned Avatar provider');
  assert(transport._avatarHost == null && transport.trustedAvatarHost === undefined,
    'the trusted Avatar provider leaked through the game transport object');
  assert(game.speech.connected === true,
    'the drawing SDK did not establish the host speech-output state bridge');
  assert(game.host.registration.gameId === 'drawing-guess'
    && game.host.registration.mode === 'development',
  'the public handshake exposed the wrong drawing identity');
  assert(game.locale === undefined && game.window === undefined,
    'drawing integration reintroduced removed locale/window SDK facades');

  const storageKey = 'settings/integration-probe';
  const storageValue = { colorHistory: ['#112233', '#abcdef'] };
  const storageSet = await game.storage.set(storageKey, storageValue);
  const storageGet = await game.storage.get(storageKey);
  const storageList = await game.storage.list({ prefix: 'settings/' });
  const qualifiedStorageKey = 'neko:minigame-storage:v1:drawing-guess:0.1.0:settings/integration-probe';
  assert(storageSet.ok === true
    && storageSet.data.stored === true
    && storageGet.ok === true
    && storageGet.data.found === true
    && storageGet.data.value.colorHistory.join(',') === '#112233,#abcdef',
  'drawing SDK storage did not round-trip a bounded preference payload');
  assert(localStorageValues.get(qualifiedStorageKey) === JSON.stringify(storageValue)
    && !localStorageValues.has(storageKey)
    && storageList.data.keys.join(',') === storageKey
    && !storageList.data.keys.includes(qualifiedStorageKey),
  'drawing storage was not namespaced or exposed its raw host key to the game');

  game.runtime.configure({ heartbeat: false, outputs: false, pageExit: false });
  const firstConsent = await game.memory.configureConsent(true, { timeoutMs: 1000 });
  assert(firstConsent.ok === true
    && firstConsent.data.enabled === true
    && game.memory.consent.configured === true
    && game.memory.consent.enabled === true
    && game.memory.consent.locked === false,
  'drawing SDK did not configure memory consent before its first runtime start');
  const started = await game.runtime.start({
    lanlan_name: 'SDK Neko',
    externalInputTakeover: false,
    external_input_takeover: false,
    game_memory_enabled: false,
    game_memory_archive_enabled: false,
    event: {
      kind: 'forged-memory-policy',
      game_memory_enabled: false,
    },
  }, { timeoutMs: 1000 });
  assert(started.ok && game.runtime.state === 'running',
    'the integrated drawing SDK runtime did not start');
  assert(game.memory.consent.enabled === true && game.memory.consent.locked === true,
    'runtime start did not lock the configured memory consent');
  const startCall = calls.find((call) => call.url.endsWith('/route/start'));
  assert(startCall?.url === '/api/game/drawing_guess/route/start',
    `the public id did not use the trusted route alias: ${startCall?.url}`);
  assert(startCall.body.session_id === 'drawing-sdk-session'
    && startCall.body.sdk_route_instance_id
    && startCall.body.game_type === 'drawing_guess'
    && startCall.body.game_memory_enabled === true
    && startCall.body.game_memory_archive_enabled === true
    && startCall.body.externalInputTakeover === false
    && startCall.body.external_input_takeover === false
    && startCall.body.event.kind === 'forged-memory-policy'
    && !Object.hasOwn(startCall.body.event, 'game_memory_enabled'),
  'the integrated host did not inject trusted route identity');

  const voiceStates = [];
  const voiceTranscripts = [];
  const voiceErrors = [];
  const unsubscribeVoiceState = game.voice.onState((voiceState) => voiceStates.push(voiceState));
  const unsubscribeVoiceTranscript = game.voice.onTranscript((transcript) => voiceTranscripts.push(transcript));
  const unsubscribeVoiceError = game.voice.onError((error) => voiceErrors.push(error));
  const voiceStarted = await game.voice.toggle({ timeoutMs: 1000 });
  const toggleRequests = voiceControlRequests.filter(request => request.action === 'toggle');
  assert(voiceControlRequests.filter(request => request.action === 'query').length === 1,
    'drawing startup did not use the bounded SDK voice-state synchronization');
  assert(voiceStarted.ok === true
    && voiceStarted.active === true
    && toggleRequests.length === 1
    && toggleRequests[0].game_type === 'drawing_guess'
    && toggleRequests[0].session_id === 'drawing-sdk-session'
    && toggleRequests[0].sdk_route_instance_id === startCall.body.sdk_route_instance_id
    && voiceStates.at(-1)?.active === true,
  'drawing voice.toggle did not use the active SDK route identity');
  windowMock.dispatchEvent(new windowMock.CustomEvent('neko-game-voice-control-message', {
    detail: {
      type: 'game_voice_transcript',
      message_id: 'drawing-voice-transcript-message',
      game_type: 'drawing_guess',
      session_id: 'drawing-sdk-session',
      sdk_route_instance_id: startCall.body.sdk_route_instance_id,
      request_id: 'drawing-voice-transcript-1',
      source: 'browser_speech_recognition',
      timestamp: 1234,
      text: '这是语音输入',
    },
  }));
  assert(voiceTranscripts.length === 1
    && voiceTranscripts[0].text === '这是语音输入'
    && voiceTranscripts[0].requestId === 'drawing-voice-transcript-1',
  'drawing voice transcript was not normalized through the SDK bridge');
  assert(voiceErrors.length === 0,
    'the retained query/start/stop/toggle voice bridge emitted a spurious error');
  const voiceStopped = await game.voice.stop({ timeoutMs: 1000 });
  assert(voiceStopped.ok === true && voiceStopped.active === false,
    'drawing voice.stop did not release the active SDK route microphone');
  unsubscribeVoiceState();
  unsubscribeVoiceTranscript();
  unsubscribeVoiceError();

  const currentCharacter = await game.avatar.getCurrentCharacter();
  const characterNames = await game.avatar.listCharacters();
  const avatarController = await game.avatar.mount({
    slot: 'drawing-guess-character',
    model: currentCharacter.model,
    viewport: { mode: 'container' },
    resize: { mode: 'container' },
  });
  avatarController.setView({ scale: 190, x: 0, y: 28 });
  avatarController.setSpeaking(true);
  assert(currentCharacter.name === 'SDK Neko'
    && currentCharacter.model.type === 'mmd'
    && currentCharacter.system_prompt === undefined
    && Object.isFrozen(currentCharacter)
    && Object.isFrozen(currentCharacter.model)
    && Object.isFrozen(characterNames)
    && characterNames.join(',') === 'SDK Neko,PNG Neko'
    && trustedAvatarMounts === 1
    && forgedAvatarMounts === 0,
  'drawing Avatar facade did not use the projected bootstrap-owned provider');
  avatarController.dispose();

  const commandResult = await game.commands.execute('round:start', {
    marker: 'drawing-command-round-trip',
    game_type: 'forged_game_type',
    session_id: 'forged-session',
    lanlan_name: 'Forged Neko',
    sdk_route_instance_id: 'forged-generation',
    game_memory_enabled: false,
    game_memory_archive_enabled: false,
    event: {
      kind: 'forged-command-policy',
      game_memory_enabled: false,
    },
  }, { timeoutMs: 1200 });
  assert(commandResult.ok === true
    && commandResult.status === 200
    && commandResult.data.command === 'round:start'
    && commandResult.data.accepted_marker === 'drawing-command-round-trip',
  'drawing commands.execute did not return the validated host response');
  const commandCall = calls.find((call) => call.url.endsWith('/round/start'));
  assert(commandCall?.url === '/api/game/drawing_guess/round/start',
    `the trusted command alias/path was not used: ${commandCall?.url}`);
  assert(commandCall.body.marker === 'drawing-command-round-trip'
    && commandCall.body.game_type === 'drawing_guess'
    && commandCall.body.session_id === 'drawing-sdk-session'
    && commandCall.body.lanlan_name === 'SDK Neko'
    && commandCall.body.sdk_route_instance_id === startCall.body.sdk_route_instance_id
    && commandCall.body.sdk_route_instance_id !== 'forged-generation'
    && commandCall.body.game_memory_enabled === true
    && commandCall.body.game_memory_archive_enabled === true
    && commandCall.body.event.kind === 'forged-command-policy'
    && !Object.hasOwn(commandCall.body.event, 'game_memory_enabled'),
  'the host did not overwrite forged command identity or memory policy');

  const reviewImage = 'data:image/jpeg;base64,YWktZHJhd2luZw==';
  const reviewResult = await game.commands.execute('round:ai-draw-review', {
    client_round_token: 1,
    image_data_url: reviewImage,
  }, { timeoutMs: 120000 });
  assert(reviewResult.ok === true && reviewResult.data.ok === true,
    'the drawing review command did not complete through the SDK');
  const reviewCall = calls.find((call) => call.url.endsWith('/ai-draw/review'));
  assert(reviewCall?.url === '/api/game/drawing_guess/ai-draw/review'
    && reviewCall.body.image_data_url === reviewImage
    && reviewCall.body.client_round_token === 1
    && reviewCall.body.game_type === 'drawing_guess'
    && reviewCall.body.session_id === 'drawing-sdk-session'
    && reviewCall.body.lanlan_name === 'SDK Neko'
    && reviewCall.body.sdk_route_instance_id === startCall.body.sdk_route_instance_id,
  'the drawing review image did not use its declared SDK route and trusted identity');

  const speechStates = [];
  const unsubscribeSpeech = game.speech.onState((playbackState) => {
    speechStates.push(playbackState);
  });
  const speechResult = await game.speech.speak({
    text: 'SDK speech bridge integration line',
    source: 'game-llm-result',
    requestId: 'drawing-sdk-speech-request',
    mirrorText: false,
    emitTurnEnd: true,
    interruptExisting: false,
  }, { timeoutMs: 1200 });
  assert(speechResult.ok === true && speechResult.data.speech_id === 'drawing-sdk-speech',
    'drawing speech.speak did not use the granted host capability');
  const speakCall = calls.find((call) => call.url.endsWith('/speak'));
  assert(speakCall?.url === '/api/game/drawing_guess/speak'
    && speakCall.body.session_id === 'drawing-sdk-session'
    && speakCall.body.game_type === 'drawing_guess'
    && speakCall.body.sdk_route_instance_id === startCall.body.sdk_route_instance_id
    && speakCall.body.wait_for_audio_completion === true,
  'speech output did not retain the trusted active route identity');
  windowMock.dispatchEvent({
    type: 'neko-speech-playback-state',
    detail: {
      type: 'speech_playback_state',
      active: true,
      speech_id: 'drawing-sdk-speech',
      remaining_seconds: 0.5,
      audio_context_state: 'running',
      updated_at: Date.now(),
    },
  });
  assert(speechStates.length === 1
    && speechStates[0].active === true
    && speechStates[0].speechId === 'drawing-sdk-speech',
  'speech.onState did not receive playback state through the host bridge');
  unsubscribeSpeech();

  const logEnabled = await game.logger.enableAfterRuntimeStart();
  assert(logEnabled?.ok === true, 'route-start logging did not create a backend log session');
  game.logger.info('runtime', 'integration_started', 'drawing SDK integration started', {
    phase: 'running',
  });
  await game.logger.flush();
  const firstLog = calls.find((call) => call.url === '/api/game/logs');
  assert(firstLog?.body.session_id === 'drawing-sdk-session'
    && firstLog.body.game_type === 'drawing_guess'
    && firstLog.body.event === 'integration_started',
  'drawing logging did not use the trusted route alias or active session');

  const ended = await game.runtime.end({ reason: 'integration-test' }, { timeoutMs: 1000 });
  assert(ended.ok && game.runtime.state === 'ended',
    'the integrated drawing SDK runtime did not end');
  const endCall = calls.find((call) => call.url.endsWith('/end'));
  assert(endCall?.url === '/api/game/drawing_guess/end'
    && endCall.body.sdk_route_instance_id === startCall.body.sdk_route_instance_id,
  'the trusted route alias or SDK route generation was lost at end');

  const sameSessionRestart = await game.runtime.start(
    { lanlan_name: 'SDK Neko' },
    { timeoutMs: 1000 },
  );
  assert(sameSessionRestart.ok && game.runtime.session.id === 'drawing-sdk-session',
    'same-session runtime restart did not preserve its identity');
  const sameSessionLogEnabled = await game.logger.enableAfterRuntimeStart();
  assert(sameSessionLogEnabled?.ok === true,
  'runtime end left the same-session backend logging gate incorrectly enabled');
  await game.runtime.end({ reason: 'integration-same-session-restart' }, { timeoutMs: 1000 });

  const resetSession = game.runtime.reset({ newSession: true });
  assert(resetSession.id && resetSession.id !== 'drawing-sdk-session',
    'runtime reset did not rotate the drawing logging session');
  const resetConsent = await game.memory.configureConsent(false, { timeoutMs: 1000 });
  assert(resetConsent.ok === true
    && resetConsent.data.enabled === false
    && game.memory.consent.configured === true
    && game.memory.consent.enabled === false
    && game.memory.consent.locked === false,
  'runtime reset did not unlock memory consent for the new session');
  const restarted = await game.runtime.start({
    lanlan_name: 'SDK Neko',
    game_memory_enabled: true,
    game_memory_archive_enabled: true,
  }, { timeoutMs: 1000 });
  assert(restarted.ok && game.runtime.state === 'running',
    'the integrated drawing SDK runtime did not restart');
  const routeStartCalls = calls.filter((call) => call.url.endsWith('/route/start'));
  const resetStartCall = routeStartCalls[routeStartCalls.length - 1];
  assert(resetStartCall.body.session_id === resetSession.id
    && resetStartCall.body.game_memory_enabled === false
    && resetStartCall.body.game_memory_archive_enabled === false,
  'the reset runtime accepted forged memory enablement');
  const secondLogEnabled = await game.logger.enableAfterRuntimeStart();
  assert(secondLogEnabled?.ok === true, 'new runtime session reused the old local logging gate');
  await game.runtime.end({ reason: 'integration-restart-test' }, { timeoutMs: 1000 });
  game.dispose();
  assert(launchNode.removed === true, 'the trusted drawing launch registration was not consumed');

  process.stdout.write('drawing-guess SDK integration test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
