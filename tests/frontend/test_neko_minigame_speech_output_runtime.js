const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function logger() {
  return {
    log() {}, info() {}, warn() {}, error() {},
    enable() {}, enableAfterRouteStart() {}, flush() {}, reset() {},
  };
}

function abortError() {
  const error = new Error('aborted');
  error.name = 'AbortError';
  return error;
}

async function main() {
  let bridgeOptions = null;
  let bridgeStarts = 0;
  let bridgeStops = 0;
  let transportDisposals = 0;
  let lastStartPayload = null;
  let nextSpeechId = 1;
  let pendingMode = false;
  let ignoreSpeechAbortMode = false;
  let ignorePreloadAbortMode = false;
  let malformedResponseId = false;
  let stateBeforeResponseMode = false;
  const speechCalls = [];
  const mirrorCalls = [];
  const preloadCalls = [];
  const pendingRequests = new Set();
  const windowMock = {
    AbortController,
    console: { error() {} },
    setInterval,
    clearInterval,
    setTimeout,
    clearTimeout,
  };
  global.window = windowMock;

  const sourcePath = path.resolve(__dirname, '../../static/game/sdk/neko-minigame-sdk.js');
  vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });

  const transport = {
    logger: logger(),
    connectGame(request) {
      return {
        accepted: true,
        protocolVersion: '1',
        hostVersion: 'speech-test-host',
        registration: {
          mode: 'registered',
          gameId: request.manifest.id,
          version: request.manifest.version,
        },
        grantedCapabilities: [
          ...request.manifest.requiredCapabilities,
          ...request.manifest.optionalCapabilities,
        ],
      };
    },
    getRuntimeState: () => ({
      sessionId: 'speech-session-1',
      characterName: '测试猫娘',
    }),
    resetRuntime: () => ({
      sessionId: 'speech-session-1',
      characterName: '测试猫娘',
    }),
    applyRuntimeState: () => ({
      sessionId: 'speech-session-1',
      characterName: '测试猫娘',
    }),
    start: async (payload) => {
      lastStartPayload = payload;
      return {
        ok: true,
        state: { game_route_active: true, session_id: 'speech-session-1', lanlan_name: '测试猫娘' },
      };
    },
    heartbeat: async () => ({ ok: true, active: true }),
    drain: async () => ({ ok: true, outputs: [] }),
    end: async () => ({ ok: true }),
    startSpeechOutputBridge(options) {
      bridgeStarts += 1;
      bridgeOptions = options;
      options.onState({
        type: 'speech_playback_state',
        active: true,
        speech_id: 'initial-speech',
        remaining_seconds: 2,
        updated_at: Date.now(),
        source: 'initial',
      }, 'initial');
      return true;
    },
    stopSpeechOutputBridge() { bridgeStops += 1; },
    requestSpeechOutput(payload, options = {}) {
      speechCalls.push({ payload, options });
      if (!pendingMode) {
        const speechId = malformedResponseId ? 'x'.repeat(129) : `speech-${nextSpeechId++}`;
        if (stateBeforeResponseMode) {
          bridgeOptions.onState({
            type: 'speech_playback_state',
            active: true,
            speech_id: speechId,
            sdk_speech_correlation_id: payload.sdk_speech_correlation_id,
            remaining_seconds: 1,
            updated_at: Date.now(),
            source: 'project-tts',
          }, 'window_event');
          bridgeOptions.onState({
            type: 'speech_playback_state',
            active: false,
            speech_id: speechId,
            sdk_speech_correlation_id: payload.sdk_speech_correlation_id,
            remaining_seconds: 0,
            updated_at: Date.now(),
            source: 'project-tts',
          }, 'window_event');
        }
        return Promise.resolve({
          ok: true,
          speech_id: speechId,
          audio_sent: true,
        });
      }
      if (ignoreSpeechAbortMode) return new Promise(() => {});
      return new Promise((resolve, reject) => {
        const entry = { resolve, reject, signal: options.signal };
        pendingRequests.add(entry);
        const rejectOnAbort = () => {
          pendingRequests.delete(entry);
          reject(abortError());
        };
        if (options.signal?.aborted) rejectOnAbort();
        else options.signal?.addEventListener('abort', rejectOnAbort, { once: true });
      });
    },
    mirrorSpeechOutput(payload, options = {}) {
      mirrorCalls.push({ payload, options });
      return Promise.resolve({ ok: true, mirrored: true });
    },
    preloadSpeechOutput(payload, options = {}) {
      preloadCalls.push({ payload, options });
      if (ignorePreloadAbortMode) return new Promise(() => {});
      return Promise.resolve({
        ok: true,
        results: payload.lines.map((_, index) => ({ index, status: 'loaded' })),
      });
    },
    dispose() { transportDisposals += 1; },
  };

  const game = await window.NekoMiniGame.connect({
    id: 'speech-output-test',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging', 'speech-output'],
  }, { transport, windowImpl: windowMock, documentImpl: {} });

  assert(game.capabilities.has('speech-output'), 'speech-output capability was not granted');
  assert(game.speech.connected, 'speech output bridge was not connected');
  assert(bridgeStarts === 1, 'speech output bridge was not started exactly once');
  assert(game.speech.getState().speechId === 'initial-speech',
    'initial host playback state was not retained by the SDK');

  const states = [];
  const errors = [];
  const unsubscribeState = game.speech.onState((state) => states.push(state));
  const unsubscribeError = game.speech.onError((error) => errors.push(error));
  const preStartSpeech = await game.speech.speak({ text: 'opening speech before route' });
  assert(preStartSpeech.ok && speechCalls.length === 1,
    'pre-route speech output was not delivered through the trusted transport');
  assert(speechCalls[0].payload.session_id === 'speech-session-1'
    && speechCalls[0].payload.lanlan_name === '测试猫娘'
    && speechCalls[0].payload.sdk_route_instance_id === undefined,
  'pre-route speech did not use the resolved session/character without inventing a route generation');
  const preStartMirror = await game.speech.mirror({ text: 'opening mirror before route' });
  assert(preStartMirror.ok && mirrorCalls.length === 1,
    'pre-route text mirror was not delivered through the trusted transport');
  assert(mirrorCalls[0].payload.session_id === 'speech-session-1'
    && mirrorCalls[0].payload.lanlan_name === '测试猫娘'
    && mirrorCalls[0].payload.sdk_route_instance_id === undefined,
  'pre-route mirror did not use the resolved session/character without inventing a route generation');
  const preStartPreload = await game.speech.preload(['opening preload before route']);
  assert(preStartPreload.ok && preloadCalls.length === 1,
    'pre-route speech preload was not delivered through the trusted transport');
  assert(preloadCalls[0].payload.session_id === 'speech-session-1'
    && preloadCalls[0].payload.lanlan_name === '测试猫娘'
    && preloadCalls[0].payload.sdk_route_instance_id === undefined,
  'pre-route preload did not use the resolved session/character without inventing a route generation');
  speechCalls.length = 0;
  mirrorCalls.length = 0;
  preloadCalls.length = 0;
  await game.runtime.start({});
  const routeInstanceId = lastStartPayload.sdk_route_instance_id;
  const response = await game.speech.speak({
    text: '  SDK 语音输出测试  ',
    requestId: 'request-1',
    source: 'quick-line',
    eventKey: 'checkpoint:ready',
    priority: 7,
    relativeGain: 1.5,
    interruptExisting: true,
    reuseSynthesizedAudio: true,
    mirrorText: true,
    emitTurnEnd: true,
    reason: 'test-priority',
    language: 'zh-CN',
    renderLanguage: 'ja-JP',
    event: { kind: 'goal', score: [1, 0] },
  });
  assert(response.ok && response.data.speech_id === 'speech-2',
    'speech output response was not normalized');
  const activeSpeechId = response.data.speech_id;
  const firstCall = speechCalls[0];
  assert(firstCall.payload.line === 'SDK 语音输出测试', 'speech text was not normalized');
  assert(firstCall.payload.session_id === 'speech-session-1',
    'runtime session was not injected into the trusted speech request');
  assert(firstCall.payload.lanlan_name === '测试猫娘',
    'runtime character was not injected into the trusted speech request');
  assert(firstCall.payload.sdk_route_instance_id === routeInstanceId,
    'speech request was not bound to the active route generation');
  assert(firstCall.payload.playback_gain === 1.5, 'relative speech gain was not forwarded');
  assert(firstCall.payload.interrupt_audio === true, 'speech interrupt policy was not forwarded');
  assert(firstCall.payload.reuse_synthesized_audio === true,
    'speech audio reuse opt-in was not forwarded');
  // game.speech.speak() is awaited by game code, so it must resolve only once
  // the line has actually been spoken. The host endpoint defaults to returning
  // as soon as the line is queued, so the SDK has to opt in explicitly --
  // otherwise two awaited speaks overlap and the second overwrites the host's
  // single speech-correlation slot, leaving the first uncancellable at route end.
  assert(firstCall.payload.wait_for_audio_completion === true,
    'speech request did not opt into waiting for playback completion');
  assert(firstCall.payload.event.kind === 'goal', 'bounded speech event data was not forwarded');
  assert(/^sdk-speech-/.test(firstCall.payload.sdk_speech_correlation_id),
    'speech request did not receive an SDK-owned playback correlation id');
  assert(firstCall.options.signal instanceof AbortSignal,
    'speech request did not receive an SDK-owned AbortSignal');

  await game.speech.speak({ text: 'use backend conversation defaults' });
  assert(speechCalls[1].payload.mirror_text === undefined
    && speechCalls[1].payload.emit_turn_end === undefined,
  'omitted speech conversation controls overrode backend defaults');

  const mirrorResponse = await game.speech.mirror({
    text: '  只镜像到主聊天  ',
    requestId: 'mirror-request-1',
    turnId: 'mirror-turn-1',
    source: 'game-llm-result',
    finalizeTurn: true,
    event: { kind: 'user-voice', hasUserSpeech: true },
  });
  assert(mirrorResponse.ok && mirrorResponse.data.mirrored === true,
    'text-only speech mirror response was not normalized');
  assert(mirrorCalls.length === 1, 'text-only speech mirror did not use the trusted transport');
  assert(mirrorCalls[0].payload.line === '只镜像到主聊天',
    'text-only speech mirror text was not normalized');
  assert(mirrorCalls[0].payload.session_id === 'speech-session-1'
    && mirrorCalls[0].payload.lanlan_name === '测试猫娘',
  'text-only speech mirror did not inject the runtime session and character');
  assert(mirrorCalls[0].payload.sdk_route_instance_id === routeInstanceId,
    'speech mirror was not bound to the active route generation');
  assert(mirrorCalls[0].payload.request_id === 'mirror-request-1'
    && mirrorCalls[0].payload.turn_id === 'mirror-turn-1'
    && mirrorCalls[0].payload.finalize_turn === true,
  'text-only speech mirror did not preserve bounded conversation metadata');
  assert(mirrorCalls[0].payload.event.kind === 'user-voice',
    'text-only speech mirror did not forward bounded event metadata');
  assert(mirrorCalls[0].options.signal instanceof AbortSignal,
    'text-only speech mirror did not receive an SDK-owned AbortSignal');

  await game.speech.mirror({ text: 'use backend finalize default' });
  assert(mirrorCalls[1].payload.finalize_turn === undefined,
    'omitted mirror finalize control overrode the backend event-derived default');

  const preloadResponse = await game.speech.preload(
    ['  预载台词一  ', '预载台词一', '预载台词二'],
    { language: 'zh-CN', renderLanguage: 'ja-JP' },
  );
  assert(preloadResponse.ok, 'speech preload response was not normalized');
  assert(preloadCalls.length === 1, 'speech preload did not use the trusted transport');
  assert(preloadCalls[0].payload.lines.join('|') === '预载台词一|预载台词二',
    'speech preload lines were not trimmed and deduplicated');
  assert(preloadCalls[0].payload.session_id === 'speech-session-1'
    && preloadCalls[0].payload.lanlan_name === '测试猫娘',
  'speech preload did not inject the runtime session and character');
  assert(preloadCalls[0].payload.sdk_route_instance_id === routeInstanceId,
    'speech preload was not bound to the active route generation');
  assert(preloadCalls[0].payload.i18n_language === 'zh-CN'
    && preloadCalls[0].payload.render_language === 'ja-JP',
  'speech preload language identity was not forwarded');
  assert(preloadCalls[0].payload.mirror_text === undefined
    && preloadCalls[0].payload.emit_turn_end === undefined,
  'speech preload leaked audible or conversation controls');
  assert(preloadCalls[0].options.signal instanceof AbortSignal,
    'speech preload request did not receive an SDK-owned AbortSignal');

  bridgeOptions.onState({
    type: 'speech_playback_state',
    active: true,
    speech_id: activeSpeechId,
    remaining_seconds: 3,
    updated_at: Date.now(),
    source: 'project-tts',
    ignored_large_field: 'x'.repeat(100000),
  }, 'window_event');
  const mappedState = states.at(-1);
  assert(mappedState.priority === 7 && mappedState.requestId === 'request-1',
    'speech request metadata was not correlated with playback state');
  assert(mappedState.eventKey === 'checkpoint:ready', 'speech event key was not retained');
  assert(mappedState.ignored_large_field === undefined,
    'unknown host playback fields escaped the bounded public state');
  bridgeOptions.onState({
    type: 'speech_playback_state',
    active: true,
    speech_id: 'pending-audio-work',
    remaining_seconds: 0,
    pending_audio_work: true,
    updated_at: Date.now(),
  }, 'window_event');
  assert(states.at(-1).active === true && states.at(-1).pendingAudioWork === true,
    'authoritative pending audio work was collapsed into an inactive snapshot');
  bridgeOptions.onState({
    type: 'speech_playback_state',
    active: true,
    speech_id: 'pending-audio-work-drained',
    remaining_seconds: 0,
    pending_audio_work: false,
    updated_at: Date.now(),
  }, 'window_event');
  assert(states.at(-1).active === false && states.at(-1).pendingAudioWork === false,
    'zero-remaining playback stayed active after pending work drained');

  stateBeforeResponseMode = true;
  const beforeResponseStateStart = states.length;
  const earlyStateResponse = await game.speech.speak({
    text: '播放状态先于 HTTP 响应',
    requestId: 'early-state-request',
    eventKey: 'early-state-event',
    priority: 8,
  });
  stateBeforeResponseMode = false;
  const earlyStates = states.slice(beforeResponseStateStart);
  assert(earlyStateResponse.ok && earlyStates.length === 2,
    'pre-response playback states were not delivered');
  assert(earlyStates.every((state) => state.priority === 8
    && state.requestId === 'early-state-request'
    && state.eventKey === 'early-state-event'),
  'pre-response playback states lost their exact request metadata');
  bridgeOptions.onState({
    type: 'speech_playback_state',
    active: true,
    speech_id: earlyStateResponse.data.speech_id,
    remaining_seconds: 1,
    updated_at: Date.now(),
  }, 'window_event');
  assert(states.at(-1).priority === null && states.at(-1).requestId === '',
    'terminal playback state was reinserted as stale metadata after the HTTP response');

  bridgeOptions.onState({
    active: true,
    speech_id: 'x'.repeat(129),
    remaining_seconds: 2,
    updated_at: Date.now(),
  }, 'window_event');
  assert(errors.at(-1)?.code === 'invalid_request',
    'invalid host playback state was not normalized as a public bridge error');
  bridgeOptions.onState({
    active: true,
    speech_id: 'finite-playback-state',
    remaining_seconds: 2,
    audio_context_time: Number.POSITIVE_INFINITY,
    scheduled_end_audio_time: Number.NaN,
    updated_at: Date.now(),
  }, 'window_event');
  assert(states.at(-1).audioContextTime === 0 && states.at(-1).scheduledEndAudioTime === 0,
    'non-finite host playback values escaped the public speech state');

  bridgeOptions.onState({
    type: 'speech_playback_state',
    active: true,
    speech_id: 'stale-suspended-speech',
    remaining_seconds: 30,
    audio_context_state: 'suspended',
    updated_at: Date.now() - 16000,
  }, 'local_storage_initial');
  assert(game.speech.getState().active === false,
    'an expired suspended playback snapshot remained active indefinitely');
  bridgeOptions.onState({
    type: 'speech_playback_state',
    active: true,
    speech_id: 'fresh-suspended-speech',
    remaining_seconds: 30,
    audio_context_state: 'suspended',
    updated_at: Date.now(),
  }, 'window_event');
  assert(game.speech.getState()?.speechId === 'fresh-suspended-speech',
    'a fresh suspended playback snapshot was rejected');

  for (let index = 0; index < 65; index += 1) {
    await game.speech.speak({
      text: `bounded metadata ${index}`,
      requestId: `bounded-${index}`,
      priority: index % 10,
    });
  }
  bridgeOptions.onState({
    active: true,
    speech_id: 'speech-1',
    remaining_seconds: 1,
    updated_at: Date.now(),
  }, 'window_event');
  assert(states.at(-1).priority === null && states.at(-1).requestId === '',
    'speech request metadata registry did not evict its oldest entry');

  let invalidGainError = null;
  try { await game.speech.speak({ text: 'invalid gain', relativeGain: 2.1 }); }
  catch (error) { invalidGainError = error; }
  assert(invalidGainError?.code === 'invalid_request', 'invalid speech gain was accepted');

  let oversizedTextError = null;
  try { await game.speech.speak({ text: 'x'.repeat(2001) }); }
  catch (error) { oversizedTextError = error; }
  assert(oversizedTextError?.code === 'invalid_request', 'oversized speech text was accepted');

  malformedResponseId = true;
  let malformedResponseError = null;
  try { await game.speech.speak({ text: 'malformed response id' }); }
  catch (error) { malformedResponseError = error; }
  malformedResponseId = false;
  assert(malformedResponseError?.code === 'invalid_request',
    'oversized host speech id escaped the bounded response registry');

  pendingMode = true;
  ignoreSpeechAbortMode = true;
  const externalController = new AbortController();
  const externallyCancelled = game.speech.speak(
    { text: 'externally cancelled request' },
    { signal: externalController.signal },
  ).then(() => null, (error) => error);
  ignoreSpeechAbortMode = false;
  await new Promise((resolve) => setImmediate(resolve));
  externalController.abort();
  assert((await externallyCancelled)?.code === 'cancelled',
    'external AbortSignal did not cancel the pending speech request');
  assert(game.speech.pendingCount === 0,
    'externally cancelled speech request was retained in the pending set');

  ignorePreloadAbortMode = true;
  const preloadController = new AbortController();
  const cancelledPreload = game.speech.preload(
    ['cancelled preload'],
    { signal: preloadController.signal },
  ).then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  preloadController.abort();
  assert((await cancelledPreload)?.code === 'cancelled',
    'SDK cancellation depended on the speech preload transport observing AbortSignal');
  assert(game.speech.preloadPendingCount === 0,
    'cancelled speech preload was retained in the pending set');
  ignorePreloadAbortMode = false;

  ignoreSpeechAbortMode = true;
  const timedOutSpeech = game.speech.speak(
    { text: 'transport ignores timeout' },
    { timeoutMs: 250 },
  ).then(() => null, (error) => error);
  ignoreSpeechAbortMode = false;
  assert((await timedOutSpeech)?.code === 'timeout',
    'SDK timeout depended on the speech transport observing AbortSignal');
  assert(game.speech.pendingCount === 0,
    'timed-out speech request was retained in the pending set');

  ignoreSpeechAbortMode = true;
  const pending = [game.speech.speak({
    text: 'dispose while transport ignores abort',
    requestId: 'pending-correlation-owner',
  })
    .then(() => null, (error) => error)];
  ignoreSpeechAbortMode = false;
  pending.push(...Array.from({ length: 3 }, (_, index) => (
    game.speech.speak({ text: `pending ${index}` }).then(() => null, (error) => error)
  )));
  await new Promise((resolve) => setImmediate(resolve));
  assert(game.speech.pendingCount === 4, 'speech pending request count changed unexpectedly');
  let busyError = null;
  try { await game.speech.speak({ text: 'fifth pending request' }); }
  catch (error) { busyError = error; }
  assert(busyError?.code === 'busy', 'speech pending request limit was not enforced');
  const firstPendingCall = speechCalls.at(-4);
  bridgeOptions.onState({
    type: 'speech_playback_state',
    active: true,
    speech_id: 'pending-correlation-speech',
    sdk_speech_correlation_id: firstPendingCall.payload.sdk_speech_correlation_id,
    remaining_seconds: 1,
    updated_at: Date.now(),
  }, 'window_event');
  assert(states.at(-1).requestId === 'pending-correlation-owner',
    'a rejected fifth request evicted metadata for an accepted pending speech request');

  game.dispose();
  const pendingErrors = await Promise.all(pending);
  assert(pendingErrors.every((error) => error?.code === 'disposed'),
    'client disposal did not classify pending speech requests as disposed');
  assert(game.speech.pendingCount === 0, 'speech pending requests were retained after disposal');
  assert(transportDisposals === 1, 'transport was not disposed exactly once');
  assert(bridgeStops === 0,
    'SDK duplicated speech bridge cleanup already owned by transport disposal');
  assert(!game.speech.connected, 'disposed speech facade remained connected');

  unsubscribeState();
  unsubscribeError();

  let optionalBridgeCleanup = 0;
  const optionalSpeech = await window.NekoMiniGame.connect({
    id: 'optional-speech-unavailable',
    version: '1',
    requiredCapabilities: ['logging'],
    optionalCapabilities: ['speech-output'],
  }, {
    transport: {
      logger: logger(),
      connectGame: transport.connectGame,
      requestSpeechOutput() { return Promise.resolve({ ok: true }); },
      preloadSpeechOutput() { return Promise.resolve({ ok: true }); },
      mirrorSpeechOutput() { return Promise.resolve({ ok: true }); },
      startSpeechOutputBridge() { return false; },
      stopSpeechOutputBridge() { optionalBridgeCleanup += 1; },
      dispose() {},
    },
    windowImpl: windowMock,
    documentImpl: {},
  });
  assert(!optionalSpeech.capabilities.has('speech-output'),
    'unavailable optional speech bridge remained granted');
  assert(optionalBridgeCleanup === 1,
    'partially started optional speech bridge was not cleaned up');
  optionalSpeech.dispose();

  let requiredBridgeCleanup = 0;
  let requiredTransportDisposals = 0;
  let requiredBridgeError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'required-speech-unavailable',
      version: '1',
      requiredCapabilities: ['logging', 'speech-output'],
    }, {
      transport: {
        logger: logger(),
        connectGame: transport.connectGame,
        requestSpeechOutput() { return Promise.resolve({ ok: true }); },
        preloadSpeechOutput() { return Promise.resolve({ ok: true }); },
        mirrorSpeechOutput() { return Promise.resolve({ ok: true }); },
        startSpeechOutputBridge() { return false; },
        stopSpeechOutputBridge() { requiredBridgeCleanup += 1; },
        dispose() { requiredTransportDisposals += 1; },
      },
      windowImpl: windowMock,
      documentImpl: {},
    });
  } catch (error) { requiredBridgeError = error; }
  assert(requiredBridgeError?.code === 'capability_unavailable',
    'unavailable required speech bridge did not reject connection');
  assert(requiredBridgeCleanup === 1 && requiredTransportDisposals === 1,
    'failed required speech bridge did not release partial host state');

  // `speech.mirror()` is on the public SpeechOutput interface unconditionally,
  // so negotiating `speech-output` on three of its four transport methods let a
  // transport that cannot mirror satisfy even a REQUIRED grant -- and every
  // mirror call on that connected client then failed `transport_unavailable`,
  // with nothing in the handshake having warned the game.
  let mirrorlessOptional = null;
  try {
    mirrorlessOptional = await window.NekoMiniGame.connect({
      id: 'mirrorless-speech-optional',
      version: '1',
      requiredCapabilities: ['logging'],
      optionalCapabilities: ['speech-output'],
    }, {
      transport: {
        logger: logger(),
        connectGame: transport.connectGame,
        requestSpeechOutput() { return Promise.resolve({ ok: true }); },
        preloadSpeechOutput() { return Promise.resolve({ ok: true }); },
        startSpeechOutputBridge() { return true; },
        stopSpeechOutputBridge() {},
        dispose() {},
      },
      windowImpl: windowMock,
      documentImpl: {},
    });
  } catch (_) { /* an optional capability must not fail the connection */ }
  assert(mirrorlessOptional && !mirrorlessOptional.capabilities.has('speech-output'),
    'a transport with no mirrorSpeechOutput was granted speech-output');
  mirrorlessOptional?.dispose();

  let mirrorlessRequiredError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'mirrorless-speech-required',
      version: '1',
      requiredCapabilities: ['logging', 'speech-output'],
    }, {
      transport: {
        logger: logger(),
        connectGame: transport.connectGame,
        requestSpeechOutput() { return Promise.resolve({ ok: true }); },
        preloadSpeechOutput() { return Promise.resolve({ ok: true }); },
        startSpeechOutputBridge() { return true; },
        stopSpeechOutputBridge() {},
        dispose() {},
      },
      windowImpl: windowMock,
      documentImpl: {},
    });
  } catch (error) { mirrorlessRequiredError = error; }
  assert(mirrorlessRequiredError?.code === 'capability_unavailable',
    'a required speech-output grant survived a transport that cannot mirror');

  // Two SDK clients on the same host route both start their correlation
  // sequence at 1, so in the same millisecond they minted the SAME id -- and
  // both resolve playback state from the shared bridge through their own
  // correlation maps. Fresh clients, so both sequences really are at 1.
  pendingMode = false;
  ignoreSpeechAbortMode = false;
  ignorePreloadAbortMode = false;
  malformedResponseId = false;
  stateBeforeResponseMode = false;
  speechCalls.length = 0;
  const correlationPeers = [];
  for (let peer = 0; peer < 2; peer += 1) {
    const peerGame = await window.NekoMiniGame.connect({
      id: 'speech-correlation-peer',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging', 'speech-output'],
    }, { transport, windowImpl: windowMock, documentImpl: {} });
    await peerGame.speech.speak({ text: `peer ${peer}` });
    correlationPeers.push(speechCalls.at(-1).payload.sdk_speech_correlation_id);
    peerGame.dispose();
  }
  const correlationParts = correlationPeers.map((id) => String(id).split('-'));
  assert(correlationParts.every((parts) => parts.length >= 5 && !!parts[4]),
    'the speech correlation id carries no per-client entropy segment');
  // The sequence segment is asserted EQUAL on purpose: it proves the two ids
  // cannot have been separated by the counter, so the entropy segment is what
  // is actually being tested.
  assert(correlationParts[0][3] === correlationParts[1][3],
    'the correlation probe did not use two fresh clients');
  assert(correlationParts[0][4] !== correlationParts[1][4],
    'two SDK clients minted the same speech correlation entropy');

  process.stdout.write('mini-game speech output runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
