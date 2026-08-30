const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function main() {
  const calls = [];
  let voiceOptions = null;
  let voiceStopped = 0;
  let disposed = 0;
  let avatarDisposed = 0;
  let avatarMountFailure = false;
  let avatarFocusFailure = false;
  let mountedAvatarConfig = null;
  const handshakeRequests = [];
  const protocolMessages = [];
  const voiceRequests = [];
  let controlBridgeOptions = null;
  let controlBridgeStopped = 0;
  let protocolPendingMode = false;
  const protocolPending = new Set();
  let dialoguePendingMode = false;
  let authorDialogueControl = { stance: 'press' };
  const dialoguePending = new Set();
  let runtimeState = { sessionId: 'sdk-test-session', characterName: '' };
  const logger = {
    log: (...args) => calls.push(['log', ...args]),
    info: (...args) => calls.push(['info', ...args]),
    warn: (...args) => calls.push(['warn', ...args]),
    error: (...args) => calls.push(['error', ...args]),
    enable: async (reason) => ({ ok: true, reason }),
    enableAfterRouteStart: async () => ({ ok: true }),
    flush: async () => ({ ok: true }),
    reset: () => calls.push(['reset']),
  };
  const transport = {
    logger,
    async connectGame(request) {
      handshakeRequests.push(request);
      return {
        accepted: true,
        protocolVersion: '1',
        hostVersion: 'test-host-1.2.3',
        registration: {
          mode: 'registered',
          gameId: request.manifest.id,
          publisherId: 'p'.repeat(100),
          version: request.manifest.version,
        },
        grantedCapabilities: [
          ...request.manifest.requiredCapabilities,
          ...request.manifest.optionalCapabilities,
        ],
      };
    },
    configureLogger: (options) => calls.push(['configure', options]),
    resetRuntime({ newSession } = {}) {
      runtimeState = {
        sessionId: newSession ? 'sdk-test-session-2' : runtimeState.sessionId,
        characterName: '',
      };
      return runtimeState;
    },
    getRuntimeState() { return runtimeState; },
    applyRuntimeState(state) {
      runtimeState = {
        ...runtimeState,
        characterName: String(state?.lanlan_name || runtimeState.characterName || ''),
      };
      return runtimeState;
    },
    start: async (payload) => ({
      ok: true,
      state: { game_route_active: true, session_id: runtimeState.sessionId },
      payload,
    }),
    end: async (payload) => ({ ok: true, payload }),
    heartbeat: async (payload) => ({ ok: true, active: true, payload }),
    drain: async (payload) => ({ ok: true, outputs: [], payload }),
    requestDialogue(payload, options = {}) {
      if (!dialoguePendingMode) {
        return Promise.resolve({
          ok: true,
          payload,
          ...(payload.prompt ? { control: authorDialogueControl } : {}),
        });
      }
      return new Promise((resolve, reject) => {
        const entry = { resolve, reject };
        dialoguePending.add(entry);
        const rejectOnAbort = () => {
          dialoguePending.delete(entry);
          const error = new Error('aborted');
          error.name = 'AbortError';
          reject(error);
        };
        if (options.signal?.aborted) rejectOnAbort();
        else options.signal?.addEventListener('abort', rejectOnAbort, { once: true });
      });
    },
    getQuickLines: async (payload) => ({
      ok: true,
      lines: { checkpoint: ['ready'] },
      payload,
    }),
    publishGameProtocol: async (kind, envelope, options = {}) => {
      protocolMessages.push({ kind, envelope, options });
      if (protocolPendingMode) {
        return new Promise((resolve, reject) => {
          const entry = { resolve, reject, signal: options.signal };
          protocolPending.add(entry);
          const rejectOnAbort = () => {
            protocolPending.delete(entry);
            const error = new Error('aborted');
            error.name = 'AbortError';
            reject(error);
          };
          if (options.signal?.aborted) rejectOnAbort();
          else options.signal?.addEventListener('abort', rejectOnAbort, { once: true });
        });
      }
      return { ok: true, accepted: true };
    },
    startGameControlBridge(options) {
      controlBridgeOptions = options;
      return true;
    },
    stopGameControlBridge() { controlBridgeStopped += 1; },
    startVoiceControlBridge(options) {
      voiceOptions = options;
      return true;
    },
    requestVoiceControl: async (action, options = {}) => {
      voiceRequests.push({ action, options });
      return {
        ok: true,
        action,
        sdk_route_instance_id: options.sdkRouteInstanceId || '',
      };
    },
    stopVoiceControlBridge() { voiceStopped += 1; },
    async mountAvatar(config) {
      if (avatarMountFailure) {
        throw Object.assign(new Error('viewport unavailable'), { code: 'viewport_unavailable' });
      }
      mountedAvatarConfig = config;
      return {
        async setModel(model) { calls.push(['avatar-model', model]); },
        focus(point) {
          if (avatarFocusFailure) {
            throw Object.assign(new Error('avatar host busy'), { code: 'busy' });
          }
          calls.push(['avatar-focus', point]);
        },
        setEmotion(name) { calls.push(['avatar-emotion', name]); },
        pause() { calls.push(['avatar-pause']); },
        resume() { calls.push(['avatar-resume']); },
        getState() { return { ready: true }; },
        dispose() { avatarDisposed += 1; },
      };
    },
    dispose() { disposed += 1; },
  };
  const windowMock = { console: { error() {} } };
  global.window = windowMock;

  const sourcePath = path.resolve(__dirname, '../../static/game/sdk/neko-minigame-sdk.js');
  vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });

  const game = await window.NekoMiniGame.connect({
    id: 'example-game',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
    optionalCapabilities: [
      'dialogue', 'quick-lines', 'voice-input', 'avatar-renderer', 'not-installed',
    ],
    contracts: {
      events: {
        'round-started': {
          type: 'object',
          properties: { round: { type: 'integer', minimum: 1, maximum: 99 } },
          required: ['round'],
        },
        // Each string is capped at 4096 by the schema default, so the byte cap
        // is only reachable in aggregate -- 256 items x 4096 is 1 MB against a
        // 256 KB limit. That makes this payload's ONLY bound the byte check.
        'round-log': {
          type: 'object',
          properties: { lines: { type: 'array', items: { type: 'string' } } },
          required: ['lines'],
        },
        'round-tag': {
          type: 'object',
          properties: { tag: { type: 'string', minLength: 1, maxLength: 4 } },
          required: ['tag'],
        },
        'round-zero': {
          type: 'object',
          properties: { delta: { type: 'number', enum: [0, 5] } },
          required: ['delta'],
        },
        'round-extra': {
          type: 'object',
          properties: { round: { type: 'integer', minimum: 0, maximum: 99 } },
          required: ['round'],
          additionalProperties: true,
        },
      },
      states: {
        score: {
          type: 'object',
          properties: {
            player: { type: 'integer', minimum: 0, maximum: 999 },
            opponent: { type: 'integer', minimum: 0, maximum: 999 },
          },
          required: ['player', 'opponent'],
        },
      },
      controls: {
        stance: ['steady', 'press', 'retreat'],
      },
      results: {
        match: {
          type: 'object',
          properties: { winner: { type: 'string', enum: ['player', 'opponent', 'draw'] } },
          required: ['winner'],
        },
      },
    },
  }, { transport });

  assert(Object.isFrozen(game), 'SDK client must be immutable');
  assert(window.NekoMiniGame.version === '0.1.0', 'public SDK version was not exposed');
  assert(handshakeRequests.length === 1, 'connect did not negotiate with the trusted host');
  assert(handshakeRequests[0].sdkVersion === window.NekoMiniGame.version,
    'host handshake did not include the SDK version');
  assert(handshakeRequests[0].protocolVersions[0] === '1',
    'host handshake did not include the supported protocol');
  assert(game.host.version === 'test-host-1.2.3', 'negotiated host version was not exposed');
  assert(game.host.registration.mode === 'registered', 'registered identity was not exposed');
  assert(Object.isFrozen(game.manifest.contracts.events['round-started']),
    'manifest contract schemas were not immutable');
  assert(game.controls.connected, 'declared control bridge was not connected');

  await game.events.emit('round-started', { round: 1 });
  await game.state.update('score', { player: 2, opponent: 1 });
  await game.results.submit('match', { winner: 'player' });
  assert(protocolMessages.length === 3, 'declared game protocol messages were not published');
  assert(protocolMessages.map((item) => item.kind).join(',') === 'event,state,result',
    'game protocol message kinds were not distinguished');
  assert(protocolMessages[0].envelope.sequence === 1
    && protocolMessages[2].envelope.sequence === 3,
  'game protocol sequence was not monotonic');
  assert(protocolMessages.every((item) => item.envelope.sessionId === 'sdk-test-session'),
    'game protocol messages did not bind the runtime session');
  let invalidEventError = null;
  // The declared payload cap is measured on the input, but what actually ships
  // is the validated copy, built from own enumerable properties. A
  // non-enumerable toJSON returning something small used to hide an oversized
  // payload from the check -- same shape as the memory.submit case, different
  // entry point.
  const emitsBeforeOversize = protocolMessages.length;
  const bulkLines = Array.from({ length: 256 }, () => 'n'.repeat(4096));
  const logDecoy = { lines: bulkLines };
  Object.defineProperty(logDecoy, 'toJSON', {
    enumerable: false,
    value: () => ({ lines: ['tiny'] }),
  });
  let oversizeContractError = null;
  try { await game.events.emit('round-log', logDecoy); }
  catch (error) { oversizeContractError = error; }
  assert(oversizeContractError?.code === 'invalid_contract',
    'a non-enumerable toJSON hid an oversized contract payload from the size limit');
  assert(protocolMessages.length === emitsBeforeOversize,
    'an oversized contract payload still reached the host');
  await game.events.emit('round-log', { lines: ['ordinary'] });
  assert(protocolMessages.length === emitsBeforeOversize + 1,
    'the size check rejected an honest contract payload');

  try { await game.events.emit('round-started', { round: 1, undeclared: true }); }
  catch (error) { invalidEventError = error; }
  assert(invalidEventError?.code === 'invalid_contract',
    'undeclared event payload fields were not rejected');

  // `Array.prototype.map` SKIPS holes, so a sparse array used to pass its
  // declared item schema without a single slot being validated -- and the holes
  // then serialize as `null`, smuggling a null past `items: {type: 'string'}`.
  const sparseLines = ['first'];
  sparseLines[3] = 'last';
  const emitsBeforeSparse = protocolMessages.length;
  let sparseArrayError = null;
  try { await game.events.emit('round-log', { lines: sparseLines }); }
  catch (error) { sparseArrayError = error; }
  assert(sparseArrayError?.code === 'invalid_contract',
    'a sparse array passed a declared item schema without its holes being validated');
  assert(protocolMessages.length === emitsBeforeSparse,
    'a sparse array payload still reached the host');
  await game.events.emit('round-log', { lines: ['first', 'second', 'third', 'last'] });
  assert(protocolMessages.length === emitsBeforeSparse + 1,
    'the sparse-slot check rejected a dense array of the same length');

  // minLength/maxLength are declared with JSON Schema's vocabulary, where both
  // count code points. Measuring UTF-16 units charged two per astral character,
  // so a maxLength:4 field rejected four emoji while accepting four letters.
  const emitsBeforeCodePoints = protocolMessages.length;
  await game.events.emit('round-tag', { tag: '🎮🎮🎮🎮' });
  assert(protocolMessages.length === emitsBeforeCodePoints + 1,
    'four astral characters were rejected by a maxLength of four');
  let tooLongTagError = null;
  try { await game.events.emit('round-tag', { tag: '🎮🎮🎮🎮🎮' }); }
  catch (error) { tooLongTagError = error; }
  assert(tooLongTagError?.code === 'invalid_contract',
    'a string past its declared code-point maximum was accepted');

  // JSON has one zero: `-0` serializes as `0` and is valid against an enum
  // declaring `0`, but `Object.is(0, -0)` is false and rejected it. `Object.is`
  // was only ever needed for NaN, and non-finite enum values are already refused
  // at manifest time.
  const emitsBeforeNegativeZero = protocolMessages.length;
  await game.events.emit('round-zero', { delta: -0 });
  assert(protocolMessages.length === emitsBeforeNegativeZero + 1,
    'a payload -0 was rejected against an enum declaring 0');
  assert(Object.is(protocolMessages.at(-1).envelope.payload.delta, -0)
    || protocolMessages.at(-1).envelope.payload.delta === 0,
  'the -0 payload was not carried through');
  // A value genuinely outside the enum is still rejected.
  let outsideEnumError = null;
  try { await game.events.emit('round-zero', { delta: 7 }); }
  catch (error) { outsideEnumError = error; }
  assert(outsideEnumError?.code === 'invalid_contract',
    'a value outside the declared enum was accepted');

  // `schema.properties` is a plain object, so an undeclared payload key named
  // after an Object.prototype member used to resolve to the inherited method --
  // truthy, so it was treated as a declared schema and `additionalProperties:
  // true` never applied. The value then failed against an `undefined` declared
  // type, with a message naming a schema nobody wrote.
  const emitsBeforeInherited = protocolMessages.length;
  await game.events.emit('round-extra', {
    round: 3,
    toString: 'label',
    valueOf: 7,
    hasOwnProperty: true,
  });
  assert(protocolMessages.length === emitsBeforeInherited + 1,
    'an undeclared key named after an Object.prototype member bypassed additionalProperties');
  const inheritedPayload = protocolMessages.at(-1).envelope.payload;
  assert(inheritedPayload.toString === 'label' && inheritedPayload.valueOf === 7
    && inheritedPayload.hasOwnProperty === true,
  'inherited-name payload fields were not carried through');
  // The reserved clone names stay rejected -- this loosens nothing.
  let reservedKeyError = null;
  try { await game.events.emit('round-extra', { round: 3, constructor: 'x' }); }
  catch (error) { reservedKeyError = error; }
  assert(reservedKeyError?.code === 'invalid_contract',
    'a clone-reserved payload key was accepted under additionalProperties');

  const controls = [];
  const controlErrors = [];
  const removeControl = game.controls.on('stance', (control) => controls.push(control));
  const removeControlError = game.controls.onError((error) => controlErrors.push(error));
  // Controls only ever originate from drain outputs, which require an active
  // route, so a control arriving without one is dropped -- same rule the voice
  // transcript and dialogue assertions below already enforce.
  controlBridgeOptions.onControl({
    protocolVersion: '1',
    sequence: 1,
    type: 'stance',
    sessionId: 'sdk-test-session',
    timestamp: 123,
    payload: 'press',
  });
  assert(controls.length === 0 && controlErrors.length === 0,
    'a host control escaped before an active runtime route existed');
  assert(game.capabilities.has('runtime'), 'required runtime capability was not granted');
  assert(game.capabilities.has('voice-input'), 'available optional voice capability was not granted');
  assert(game.capabilities.has('avatar-renderer'), 'available avatar capability was not granted');
  assert(!game.capabilities.has('not-installed'), 'unsupported optional capability was granted');
  assert(typeof voiceOptions?.onTranscript === 'function', 'host transcript callback was not connected');

  const transcriptEvents = [];
  const removeTranscript = game.voice.onTranscript((event) => transcriptEvents.push(event));
  voiceOptions.onTranscript({
    text: '  final words  ',
    request_id: 'voice-1',
    source: 'voice',
    timestamp: 123,
  });
  assert(transcriptEvents.length === 0, 'voice transcript escaped before an active runtime route existed');
  let inactiveVoiceError = null;
  try { await game.voice.toggle(); }
  catch (error) { inactiveVoiceError = error; }
  assert(inactiveVoiceError?.code === 'invalid_state' && voiceRequests.length === 0,
    'voice control reached the host before an active runtime route existed');
  let inactiveDialogueError = null;
  try { await game.dialogue.request({ event: 'before-start' }); }
  catch (error) { inactiveDialogueError = error; }
  assert(inactiveDialogueError?.code === 'invalid_state',
    'dialogue request was allowed before an active runtime route existed');
  const started = await game.runtime.start({ mode: 'default' });
  assert(started.data.payload.mode === 'default', 'runtime start did not use the host transport');
  const routeInstanceId = started.data.payload.sdk_route_instance_id;
  // Validation and delivery, now that a route actually exists.
  controlBridgeOptions.onControl({
    protocolVersion: '1',
    sequence: 2,
    type: 'stance',
    sessionId: 'sdk-test-session',
    routeInstanceId,
    timestamp: 123,
    payload: 'press',
  });
  controlBridgeOptions.onControl({
    protocolVersion: '1',
    sequence: 2,
    type: 'stance',
    sessionId: 'sdk-test-session',
    routeInstanceId,
    payload: 'retreat',
  });
  assert(controls.length === 1 && controls[0].payload === 'press',
    'declared host control was not validated and delivered exactly once');
  controlBridgeOptions.onControl({
    protocolVersion: '1',
    sequence: 3,
    type: 'stance',
    sessionId: 'sdk-test-session',
    routeInstanceId,
    payload: 'not-declared',
  });
  assert(controlErrors.length === 1 && controlErrors[0].code === 'invalid_contract',
    'invalid host control did not produce a bounded public error');
  controlBridgeOptions.onControl({
    protocolVersion: '1',
    sequence: 4,
    type: 'stance',
    sessionId: 'sdk-test-session',
    routeInstanceId: 'stale-route-instance',
    payload: 'press',
  });
  assert(controls.length === 1,
    'a stale route-generation control was delivered to the replacement route');
  assert(controlErrors.length === 2 && controlErrors.at(-1).code === 'session_invalid',
    'a stale route-generation control did not produce a bounded public error');
  controlBridgeOptions.onControl({
    protocolVersion: '1',
    sequence: 5,
    type: 'stance',
    sessionId: 'sdk-test-session',
    routeInstanceId,
    payload: 'press',
  });
  assert(controls.length === 2 && controls.at(-1).routeInstanceId === routeInstanceId,
    'the active route-generation control was not delivered with its identity');
  removeControl();
  removeControlError();
  voiceOptions.onTranscript({
    text: 'stale words',
    request_id: 'voice-stale',
    source: 'voice',
    timestamp: 122,
    sdk_route_instance_id: 'stale-route-instance',
  });
  voiceOptions.onTranscript({
    text: '  final words  ',
    request_id: 'voice-1',
    source: 'voice',
    timestamp: 123,
    sdk_route_instance_id: routeInstanceId,
  });
  assert(transcriptEvents.length === 1, 'active-route final transcript was not emitted exactly once');
  assert(transcriptEvents[0].text === 'final words', 'transcript was not normalized');
  assert(transcriptEvents[0].requestId === 'voice-1', 'transcript request id was not normalized');
  removeTranscript();
  voiceOptions.onTranscript({ text: 'after unsubscribe', sdk_route_instance_id: routeInstanceId });
  assert(transcriptEvents.length === 1, 'unsubscribe did not release transcript listener');
  const voiceState = await game.voice.toggle();
  assert(voiceState.action === 'toggle', 'voice toggle did not use the host transport');
  assert(voiceRequests.at(-1).options.sdkRouteInstanceId === routeInstanceId,
    'voice control was not bound to the active route generation');
  const dialogue = await game.dialogue.request({ event: 'checkpoint' });
  assert(dialogue.data.payload.event === 'checkpoint', 'dialogue request did not use the host transport');
  assert(dialogue.data.payload.session_id === 'sdk-test-session',
    'dialogue request did not bind the trusted runtime session');
  assert(dialogue.data.payload.sdk_route_instance_id === routeInstanceId,
    'dialogue request was not bound to the active route generation');
  await game.events.emit('round-started', { round: 2 });
  assert(protocolMessages.at(-1).envelope.sdk_route_instance_id === routeInstanceId,
    'game protocol message was not bound to the active route generation');
  protocolMessages.pop();
  const quickLines = await game.dialogue.quickLines({ mode: 'practice' });
  assert(quickLines.data.lines.checkpoint[0] === 'ready',
    'quick lines did not use the host transport');
  assert(quickLines.data.payload.session_id === 'sdk-test-session',
    'quick lines did not bind the trusted runtime session');
  const authorDialogue = await game.dialogue.request({
    event: { kind: 'checkpoint' },
    prompt: {
      mode: 'author-managed',
      messages: [
        { role: 'system', content: 'stable game rules' },
        { role: 'user', content: 'round context' },
        { role: 'assistant', content: 'previous line' },
        { role: 'user', content: 'current event' },
      ],
    },
  });
  const authorMessages = authorDialogue.data.payload.prompt.messages;
  assert(authorMessages.map((item) => item.role).join(',') === 'system,user,assistant,user',
    'author-managed dialogue message order was changed');
  assert(authorMessages[0].content === 'stable game rules',
    'author-managed dialogue content was changed');
  assert(Object.isFrozen(authorMessages) && Object.isFrozen(authorMessages[0]),
    'author-managed dialogue messages were not immutable');
  assert(authorDialogue.data.control.stance === 'press'
    && Object.isFrozen(authorDialogue.data.control),
  'author-managed dialogue controls were not validated and frozen by the manifest contract');

  authorDialogueControl = { undeclared: 'forged' };
  let undeclaredAuthorControlError = null;
  try {
    await game.dialogue.request({
      event: { kind: 'checkpoint' },
      prompt: {
        mode: 'author-managed',
        messages: [{ role: 'user', content: 'return an invalid control' }],
      },
    });
  } catch (error) { undeclaredAuthorControlError = error; }
  assert(undeclaredAuthorControlError?.code === 'invalid_contract',
    'author-managed dialogue accepted an undeclared control field');
  authorDialogueControl = { stance: 'press' };

  for (const forbiddenPayload of [
    { event: 'checkpoint', system_prompt: 'replace host rules' },
    { event: 'checkpoint', provider: 'custom-provider' },
    { event: 'checkpoint', messages: [{ role: 'user', content: 'bypass prompt envelope' }] },
  ]) {
    let forbiddenPromptError = null;
    try { await game.dialogue.request(forbiddenPayload); }
    catch (error) { forbiddenPromptError = error; }
    assert(forbiddenPromptError?.code === 'invalid_request',
      'game dialogue was allowed to provide a host-controlled field');
  }

  let invalidAuthorPromptError = null;
  try {
    await game.dialogue.request({
      event: 'checkpoint',
      prompt: {
        mode: 'author-managed',
        messages: [{ role: 'tool', content: 'unsupported role' }],
      },
    });
  } catch (error) { invalidAuthorPromptError = error; }
  assert(invalidAuthorPromptError?.code === 'invalid_request',
    'author-managed dialogue accepted an unsupported role');

  // `Array.prototype.map` SKIPS holes, so a sparse author-managed message array
  // was accepted and frozen without a single slot being validated. JSON
  // transport then turned the hole into `null`, and the backend rejected with
  // HTTP 400 what the SDK had just admitted locally -- the game gets a server
  // error where it should have got `invalid_request` before any request left.
  const sparseMessages = [{ role: 'user', content: 'first' }];
  sparseMessages[2] = { role: 'user', content: 'third' };
  let sparsePromptError = null;
  try {
    await game.dialogue.request({
      event: 'checkpoint',
      prompt: { mode: 'author-managed', messages: sparseMessages },
    });
  } catch (error) { sparsePromptError = error; }
  assert(sparsePromptError?.code === 'invalid_request',
    'author-managed dialogue accepted a sparse message array without validating its holes');
  // The dense array of the same length still goes through.
  await game.dialogue.request({
    event: 'checkpoint',
    prompt: {
      mode: 'author-managed',
      messages: [
        { role: 'user', content: 'first' },
        { role: 'user', content: 'second' },
        { role: 'user', content: 'third' },
      ],
    },
  });

  dialoguePendingMode = true;
  const boundedDialogueRequests = Array.from({ length: 4 }, (_, index) => (
    game.dialogue.request({ event: `pending-${index}` }).then(() => null, (error) => error)
  ));
  await new Promise((resolve) => setImmediate(resolve));
  assert(game.dialogue.pendingCount === 4 && dialoguePending.size === 4,
    'dialogue pending requests were not tracked');
  let dialogueBusyError = null;
  try { await game.dialogue.request({ event: 'fifth-pending' }); }
  catch (error) { dialogueBusyError = error; }
  assert(dialogueBusyError?.code === 'busy', 'dialogue pending request growth was not bounded');
  for (const entry of Array.from(dialoguePending)) {
    dialoguePending.delete(entry);
    entry.resolve({ ok: true });
  }
  await Promise.all(boundedDialogueRequests);
  assert(game.dialogue.pendingCount === 0, 'completed dialogue requests remained resident');
  dialoguePendingMode = false;
  transport.requestDialogue = async () => {
    throw Object.assign(new Error('host timed out'), { code: 'timeout', internal: 'hidden' });
  };
  let dialogueError = null;
  try { await game.dialogue.request({ event: 'timeout' }); }
  catch (error) { dialogueError = error; }
  assert(dialogueError instanceof window.NekoMiniGame.Error,
    'transport failures were not normalized to the public SDK error');
  assert(dialogueError.code === 'timeout' && dialogueError.details.operation === 'dialogue.request',
    'public transport error lost its stable code or operation');
  assert(dialogueError.details.internal === undefined,
    'public transport error leaked transport-specific details');
  game.logger.info('runtime', 'ready', 'ready');
  assert(calls.some((entry) => entry[0] === 'info'), 'SDK logger did not use the required host logger');

  const avatar = await game.avatar.mount({
    slot: 'ai',
    model: { type: 'live2d', path: '/models/ai.model3.json' },
    viewport: { mode: 'fixed', width: 200, height: 300 },
    fit: { mode: 'contain', align: 'bottom-center', padding: 6, scaleMultiplier: 1 },
    resize: { mode: 'fixed' },
  });
  assert(Object.isFrozen(avatar), 'avatar controller must be immutable');
  assert(game.avatar.activeCount === 1, 'active avatar controller was not tracked');
  assert(mountedAvatarConfig?.viewport?.width === 200, 'avatar viewport was not normalized');
  assert(mountedAvatarConfig?.resize?.mode === 'fixed', 'fixed resize policy was not forwarded');
  assert(Object.isFrozen(mountedAvatarConfig.fit), 'avatar layout contract must be immutable');
  avatar.focus({ x: 12, y: 34 });
  avatar.setEmotion('smile');
  await avatar.setModel({ type: 'vrm', path: '/models/ai.vrm' });
  assert(calls.some((entry) => entry[0] === 'avatar-focus' && entry[1].x === 12),
    'avatar focus did not use the host controller');
  assert(calls.some((entry) => entry[0] === 'avatar-model' && entry[1].type === 'vrm'),
    'avatar model switch did not use the host controller');
  const boundedAvatars = [avatar];
  for (let index = 1; index < 8; index += 1) {
    boundedAvatars.push(await game.avatar.mount({
      slot: `slot-${index}`,
      model: { type: 'live2d', path: `/models/${index}.model3.json` },
      viewport: { mode: 'fixed', width: 200, height: 300 },
      resize: { mode: 'fixed' },
    }));
  }
  let avatarLimitError = null;
  try {
    await game.avatar.mount({
      slot: 'overflow',
      model: { type: 'vrm', path: '/models/overflow.vrm' },
      viewport: { mode: 'fixed', width: 200, height: 300 },
      resize: { mode: 'fixed' },
    });
  } catch (error) {
    avatarLimitError = error;
  }
  assert(avatarLimitError?.code === 'busy', 'avatar renderer growth was not bounded');
  boundedAvatars[0].dispose();
  assert(game.avatar.activeCount === 7, 'explicit avatar disposal did not release its slot');
  avatarMountFailure = true;
  let avatarMountTransportError = null;
  try {
    await game.avatar.mount({
      slot: 'failed-host-mount',
      model: { type: 'vrm', path: '/models/failed.vrm' },
      viewport: { mode: 'fixed', width: 200, height: 300 },
      resize: { mode: 'fixed' },
    });
  } catch (error) { avatarMountTransportError = error; }
  avatarMountFailure = false;
  assert(avatarMountTransportError instanceof window.NekoMiniGame.Error
    && avatarMountTransportError.code === 'request_failed'
    && avatarMountTransportError.details.operation === 'avatar.mount'
    && game.avatar.activeCount === 7,
  'avatar mount leaked a host error or retained a failed controller');
  avatarFocusFailure = true;
  let avatarControllerTransportError = null;
  try { boundedAvatars[1].focus({ x: 1, y: 2 }); }
  catch (error) { avatarControllerTransportError = error; }
  avatarFocusFailure = false;
  assert(avatarControllerTransportError instanceof window.NekoMiniGame.Error
    && avatarControllerTransportError.code === 'busy'
    && avatarControllerTransportError.details.operation === 'avatar.focus',
  'avatar controller leaked a host-specific error');

  const stateListeners = [];
  for (let index = 0; index < 32; index += 1) {
    stateListeners.push(game.voice.onState(() => {}));
  }
  let listenerLimitError = null;
  try { game.voice.onState(() => {}); }
  catch (error) { listenerLimitError = error; }
  assert(listenerLimitError?.code === 'busy', 'listener growth was not bounded');
  stateListeners.forEach((unsubscribe) => unsubscribe());

  protocolPendingMode = true;
  const pendingProtocolRequests = Array.from({ length: 8 }, (_, index) => (
    game.events.emit('round-started', { round: index + 1 })
      .then(() => null, (error) => error)
  ));
  await new Promise((resolve) => setImmediate(resolve));
  assert(protocolPending.size === 8, 'game protocol pending requests were not retained for the test');
  let protocolBusyError = null;
  try { await game.events.emit('round-started', { round: 9 }); }
  catch (error) { protocolBusyError = error; }
  assert(protocolBusyError?.code === 'busy', 'game protocol pending request growth was not bounded');

  game.dispose();
  const pendingProtocolErrors = await Promise.all(pendingProtocolRequests);
  assert(pendingProtocolErrors.every((error) => error?.code === 'disposed'),
    'client disposal did not cancel game protocol requests as disposed');
  assert(protocolPending.size === 0, 'disposed game protocol requests remained in the host transport');
  assert(game.disposed, 'dispose did not update the client state');
  assert(voiceStopped === 0, 'SDK duplicated transport-owned voice cleanup');
  assert(controlBridgeStopped === 0, 'SDK duplicated transport-owned control cleanup');
  assert(disposed === 1, 'dispose did not release the injected transport');
  assert(avatarDisposed === 8, 'dispose did not release active avatar controllers');
  let disposedError = null;
  try { await game.voice.toggle(); }
  catch (error) { disposedError = error; }
  assert(disposedError?.code === 'disposed', 'calls after dispose did not fail predictably');

  let partialVoiceCleanup = 0;
  const optionalVoiceUnavailable = await window.NekoMiniGame.connect({
    id: 'no-voice-route',
    version: '1',
    requiredCapabilities: ['runtime', 'logging'],
    optionalCapabilities: ['voice-input'],
  }, {
    transport: {
      ...transport,
      startVoiceControlBridge: () => false,
      stopVoiceControlBridge: () => { partialVoiceCleanup += 1; },
      dispose() {},
    },
  });
  assert(!optionalVoiceUnavailable.capabilities.has('voice-input'),
    'failed optional voice transport remained granted');
  assert(partialVoiceCleanup === 1, 'partial optional voice bridge was not cleaned up');
  optionalVoiceUnavailable.dispose();

  let missingRequiredDisposed = 0;
  let requiredError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'missing-dialogue',
      version: '1',
      requiredCapabilities: ['dialogue', 'logging'],
    }, {
      transport: {
        logger,
        connectGame: transport.connectGame,
        dispose() { missingRequiredDisposed += 1; },
      },
    });
  } catch (error) {
    requiredError = error;
  }
  assert(requiredError?.code === 'capability_unavailable', 'missing required capability was not rejected');
  assert(missingRequiredDisposed === 1, 'missing required capability did not release the transport');

  let mandatoryError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'no-official-logger',
      version: '1',
      requiredCapabilities: ['runtime'],
    }, { transport });
  } catch (error) {
    mandatoryError = error;
  }
  assert(mandatoryError?.code === 'invalid_manifest', 'official logging was not mandatory');

  let quickLinesDependencyError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'quick-lines-without-dialogue',
      version: '1',
      requiredCapabilities: ['logging'],
      optionalCapabilities: ['quick-lines'],
    }, { transport });
  } catch (error) {
    quickLinesDependencyError = error;
  }
  assert(quickLinesDependencyError?.code === 'invalid_manifest',
    'quick-lines was accepted without the dialogue capability');

  // The JSON schema carries this dependency too, but normalizeManifest() is the
  // path a real connection runs, so the rule has to be enforced here as well.
  // `voice-input` without `runtime` is permanently unusable: every voice.*
  // command goes through requireActiveRuntimeRoute(), and a game with no
  // runtime API can never establish that route. `speech-output` is excluded on
  // purpose -- speech.speak() works before a route exists.
  for (const voiceCapability of ['voice-input']) {
    let voiceDependencyError = null;
    try {
      await window.NekoMiniGame.connect({
        id: `${voiceCapability}-without-runtime`,
        version: '1',
        requiredCapabilities: ['logging', voiceCapability],
      }, { transport });
    } catch (error) {
      voiceDependencyError = error;
    }
    assert(voiceDependencyError?.code === 'invalid_manifest',
      `${voiceCapability} was accepted without the runtime capability`);
  }

  let unknownManifestFieldError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'unknown-manifest-field',
      version: '1',
      requiredCapabilities: ['logging'],
      undocumentedExtension: true,
    }, { transport });
  } catch (error) {
    unknownManifestFieldError = error;
  }
  assert(unknownManifestFieldError?.code === 'invalid_manifest'
    && unknownManifestFieldError?.details?.field === 'undocumentedExtension',
  'runtime manifest validation did not reject an unknown top-level field');

  let versionError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'future-game',
      version: '1',
      protocolVersion: '999',
    }, { transport });
  } catch (error) {
    versionError = error;
  }
  assert(versionError?.code === 'incompatible_version', 'incompatible protocol was not rejected');

  let unregisteredDisposed = 0;
  let unregisteredError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'unregistered-game',
      version: '1.0.0',
      requiredCapabilities: ['logging'],
    }, {
      transport: {
        connectGame: async () => ({
          accepted: false,
          code: 'game_unregistered',
          message: 'not installed',
        }),
        dispose() { unregisteredDisposed += 1; },
      },
    });
  } catch (error) {
    unregisteredError = error;
  }
  assert(unregisteredError?.code === 'game_unregistered',
    'an unregistered formal game was not rejected');
  assert(unregisteredDisposed === 1, 'rejected handshake did not release the transport');

  const developmentGame = await window.NekoMiniGame.connect({
    id: 'local-development-game',
    version: '0.0.1',
    requiredCapabilities: ['logging'],
  }, {
    transport: {
      logger,
      async connectGame(request) {
        return {
          accepted: true,
          protocolVersion: '1',
          hostVersion: 'test-development-host',
          registration: {
            mode: 'development',
            gameId: request.manifest.id,
            publisherId: '',
            version: request.manifest.version,
          },
          grantedCapabilities: ['logging'],
        };
      },
      dispose() {},
    },
  });
  assert(developmentGame.host.registration.mode === 'development',
    'host-approved development identity was not preserved');
  developmentGame.dispose();

  let hostProtocolError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'protocol-mismatch',
      version: '1.0.0',
      requiredCapabilities: ['logging'],
    }, {
      transport: {
        connectGame: async (request) => ({
          accepted: true,
          protocolVersion: '2',
          hostVersion: 'future-host',
          registration: {
            mode: 'registered',
            gameId: request.manifest.id,
            version: request.manifest.version,
          },
          grantedCapabilities: ['logging'],
        }),
        dispose() {},
      },
    });
  } catch (error) {
    hostProtocolError = error;
  }
  assert(hostProtocolError?.code === 'incompatible_version',
    'an incompatible host-selected protocol was not rejected');

  // Placed last: these connect extra clients, and every assertion above counts
  // handshakes and protocol messages on the shared transport.

  // The published schema declares minimum/maximum as numbers. `Number()`
  // coercion accepted a numeric-looking string, and turned `minimum: null` --
  // an author writing "no minimum" -- into a hard floor of 0, invisible until a
  // payload was rejected against a bound nobody declared.
  for (const badLimit of ['5', null, true, []]) {
    let badLimitError = null;
    try {
      await window.NekoMiniGame.connect({
        id: 'limit-type-test',
        version: '1.0.0',
        requiredCapabilities: ['runtime', 'logging'],
        contracts: {
          events: {
            'round-count': {
              type: 'object',
              properties: { round: { type: 'integer', minimum: badLimit } },
              required: ['round'],
            },
          },
        },
      }, { transport });
    } catch (error) { badLimitError = error; }
    assert(badLimitError?.code === 'invalid_manifest',
      `a non-number minimum (${JSON.stringify(badLimit)}) was coerced instead of rejected`);
  }

  // The published schema declares id/version/protocolVersion as strings and both
  // capability lists as uniqueItems arrays of strings. `String()` coercion and a
  // silent de-duplication meant manifests the schema rejects still connected --
  // so validating in an editor or in CI disagreed with what actually runs.
  const MANIFEST_TYPE_CASES = [
    ['version', 1],
    ['version', true],
    ['protocolVersion', 1],
    ['id', 1],
  ];
  for (const [field, badValue] of MANIFEST_TYPE_CASES) {
    let typeError = null;
    try {
      await window.NekoMiniGame.connect({
        id: 'manifest-type-test',
        version: '1.0.0',
        requiredCapabilities: ['logging'],
        [field]: badValue,
      }, { transport });
    } catch (error) { typeError = error; }
    assert(typeError && typeError.code !== undefined,
      `a non-string ${field} (${JSON.stringify(badValue)}) was coerced instead of rejected`);
    assert(['invalid_manifest', 'incompatible_version'].includes(typeError.code),
      `a non-string ${field} produced an unexpected error code: ${typeError.code}`);
  }
  let duplicateCapabilityError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'duplicate-capability-test',
      version: '1.0.0',
      requiredCapabilities: ['logging', 'logging'],
    }, { transport });
  } catch (error) { duplicateCapabilityError = error; }
  assert(duplicateCapabilityError?.code === 'invalid_manifest',
    'a duplicate capability was silently de-duplicated instead of rejected');
  // Deliberately an object that stringifies to a VALID capability name: a value
  // that stringifies to an invalid one is rejected by the pattern either way, so
  // it cannot tell coercion from a type check.
  let nonStringCapabilityError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'nonstring-capability-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', { toString: () => 'logging' }],
    }, { transport });
  } catch (error) { nonStringCapabilityError = error; }
  assert(nonStringCapabilityError?.code === 'invalid_manifest',
    'a non-string capability entry was coerced instead of rejected');
  // An explicitly supplied falsey protocolVersion must not be replaced by the
  // default: `|| SDK_PROTOCOL_VERSION` swallowed both `0` and `''`.
  // `' 1 '` included: the schema pins this with `const: "1"`, so a padded value
  // is schema-invalid -- and trimming first turned it into the supported version.
  for (const badProtocol of [0, '', '   ', ' 1 ', '1 ']) {
    let badProtocolError = null;
    try {
      await window.NekoMiniGame.connect({
        id: 'protocol-default-test',
        version: '1.0.0',
        protocolVersion: badProtocol,
        requiredCapabilities: ['logging'],
      }, { transport });
    } catch (error) { badProtocolError = error; }
    assert(['invalid_manifest', 'incompatible_version'].includes(badProtocolError?.code),
      `an explicitly falsey protocolVersion (${JSON.stringify(badProtocol)}) was replaced by the default`);
  }
  // An absent one still defaults.
  const defaultedProtocolGame = await window.NekoMiniGame.connect({
    id: 'protocol-absent-test',
    version: '1.0.0',
    requiredCapabilities: ['logging'],
  }, { transport });
  assert(defaultedProtocolGame.manifest.protocolVersion === '1',
    'an absent protocolVersion stopped defaulting');
  defaultedProtocolGame.dispose();

  // Same rule for every integer limit: the published schema declares
  // minLength/maxLength/minItems/maxItems/maxEntries as JSON integers, and
  // `Number()` accepted '5' and true alike -- so validating a manifest against
  // the schema in an editor or in CI disagreed with the validation that runs.
  const INTEGER_LIMIT_CASES = [
    ['minLength', '5'],
    ['maxLength', true],
    ['minItems', '0'],
    ['maxItems', true],
  ];
  for (const [field, badValue] of INTEGER_LIMIT_CASES) {
    const arrayShaped = field === 'minItems' || field === 'maxItems';
    let coercedError = null;
    try {
      await window.NekoMiniGame.connect({
        id: 'integer-limit-test',
        version: '1.0.0',
        requiredCapabilities: ['runtime', 'logging'],
        contracts: {
          events: {
            'limit-probe': arrayShaped
              ? { type: 'array', items: { type: 'string' }, [field]: badValue }
              : { type: 'string', [field]: badValue },
          },
        },
      }, { transport });
    } catch (error) { coercedError = error; }
    assert(coercedError?.code === 'invalid_manifest',
      `a coerced ${field} (${JSON.stringify(badValue)}) was accepted instead of rejected`);
  }
  let coercedMaxEntriesError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'integer-maxentries-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      optionalCapabilities: ['leaderboard-local'],
      leaderboards: {
        main: { scoreField: 'score', order: 'descending', maxEntries: '20', retention: 'recent' },
      },
    }, { transport });
  } catch (error) { coercedMaxEntriesError = error; }
  assert(coercedMaxEntriesError?.code === 'invalid_manifest',
    'a coerced leaderboard maxEntries was accepted instead of rejected');

  // `?? {}` also swallowed an explicit null, which the schema types as an object
  // and rejects -- so a schema-invalid manifest connected as though the author
  // had declared nothing at all.
  for (const field of ['contracts', 'leaderboards']) {
    let nullContainerError = null;
    try {
      await window.NekoMiniGame.connect({
        id: 'null-container-test',
        version: '1.0.0',
        requiredCapabilities: ['logging'],
        [field]: null,
      }, { transport });
    } catch (error) { nullContainerError = error; }
    assert(nullContainerError?.code === 'invalid_manifest',
      `an explicit null ${field} was treated as absent`);
  }

  // Same family, four more sites that still defaulted on an explicit null.
  // The schema types both capability lists as arrays, each contract KIND as an
  // object, and `properties` / `required` inside an object contract as object
  // and array -- none of them accept null.
  for (const field of ['requiredCapabilities', 'optionalCapabilities']) {
    let nullCapabilityError = null;
    try {
      await window.NekoMiniGame.connect({
        id: 'null-capability-test',
        version: '1.0.0',
        requiredCapabilities: ['logging'],
        [field]: null,
      }, { transport });
    } catch (error) { nullCapabilityError = error; }
    assert(nullCapabilityError?.code === 'invalid_manifest',
      `an explicit null ${field} was treated as absent`);
  }
  let nullContractKindError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'null-kind-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      contracts: { events: null },
    }, { transport });
  } catch (error) { nullContractKindError = error; }
  assert(nullContractKindError?.code === 'invalid_manifest',
    'an explicit null contract kind was treated as absent');
  // `properties: null` must be tested WITHOUT `required`: with a `required`
  // list present the manifest is rejected anyway because the named field is
  // not declared, which satisfies the assertion for the wrong reason (this
  // exact shape survived its mutation before the split).
  let nullPropertiesError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'null-properties-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      contracts: { events: { 'null-props': { type: 'object', properties: null } } },
    }, { transport });
  } catch (error) { nullPropertiesError = error; }
  assert(nullPropertiesError?.code === 'invalid_manifest',
    'an explicit null contract properties was treated as absent');
  let nullRequiredError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'null-required-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      contracts: {
        events: {
          'null-required': {
            type: 'object',
            properties: { round: { type: 'integer', minimum: 1, maximum: 99 } },
            required: null,
          },
        },
      },
    }, { transport });
  } catch (error) { nullRequiredError = error; }
  assert(nullRequiredError?.code === 'invalid_manifest',
    'an explicit null contract required was treated as absent');
  // Control: the same manifest with those fields ABSENT must still connect,
  // otherwise the four assertions above are satisfied by an implementation
  // that simply stopped accepting object contracts at all.
  const absentFieldClient = await window.NekoMiniGame.connect({
    id: 'absent-contract-field-test',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
    contracts: { events: { 'no-fields': { type: 'object' } } },
  }, { transport });
  assert(!!absentFieldClient, 'absent contract properties/required stopped connecting');
  absentFieldClient.dispose();

  // The schema bounds enum SHORTHAND items at maxLength 4096; the expanded
  // `enum` form carries no such bound, so converting first dropped it.
  let longShorthandError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'long-shorthand-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      contracts: { controls: { stance: ['ready', 'z'.repeat(4097)] } },
    }, { transport });
  } catch (error) { longShorthandError = error; }
  assert(longShorthandError?.code === 'invalid_manifest',
    'an over-long enum shorthand value was accepted');
  // Exactly at the bound is still fine.
  const boundedShorthandGame = await window.NekoMiniGame.connect({
    id: 'bounded-shorthand-test',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
    contracts: { controls: { stance: ['ready', 'z'.repeat(4096)] } },
  }, { transport });
  boundedShorthandGame.dispose();

  // `version` has no pattern in the schema, so `' 1.0 '` is schema-VALID and
  // distinct from `'1.0'`; trimming aliased two declared identities into one.
  const paddedVersionGame = await window.NekoMiniGame.connect({
    id: 'padded-version-test',
    version: ' 1.0 ',
    requiredCapabilities: ['logging'],
  }, { transport });
  assert(paddedVersionGame.manifest.version === ' 1.0 ',
    'a schema-valid padded version was silently aliased onto a different one');
  paddedVersionGame.dispose();

  // The schema requires an exact enum member for `type`, and its `id` pattern
  // applies to the declared string. Trimming first turned `' string '` into a
  // supported type, and remapped `' demo '` onto the registration and storage
  // identity of the real `demo`.
  let paddedTypeError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'padded-type-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      contracts: { events: { probe: { type: ' string ' } } },
    }, { transport });
  } catch (error) { paddedTypeError = error; }
  assert(paddedTypeError?.code === 'invalid_manifest',
    'a whitespace-padded contract type was trimmed into a supported one');
  let paddedIdError = null;
  try {
    await window.NekoMiniGame.connect({
      id: ' padded-id-test ',
      version: '1.0.0',
      requiredCapabilities: ['logging'],
    }, { transport });
  } catch (error) { paddedIdError = error; }
  assert(paddedIdError?.code === 'invalid_manifest',
    'a whitespace-padded manifest id was trimmed onto another identity');

  // Both enum definitions in the schema are `uniqueItems: true`.
  let duplicateEnumError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'duplicate-enum-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      contracts: { events: { probe: { type: 'string', enum: ['a', 'b', 'a'] } } },
    }, { transport });
  } catch (error) { duplicateEnumError = error; }
  assert(duplicateEnumError?.code === 'invalid_manifest',
    'a duplicate contract enum value was silently de-duplicated');
  // The shorthand form goes through the same normalizer.
  let duplicateShorthandError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'duplicate-shorthand-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      contracts: { controls: { stance: ['ready', 'ready'] } },
    }, { transport });
  } catch (error) { duplicateShorthandError = error; }
  assert(duplicateShorthandError?.code === 'invalid_manifest',
    'a duplicate enum shorthand value was silently de-duplicated');

  // The schema applies its capability pattern to the string the manifest
  // actually declares, so `' logging '` is schema-invalid -- while trimming
  // first silently rewrote it into a real permission request.
  // Each case declares whatever ELSE it needs unpadded, so the only reason the
  // manifest can fail is the padding itself. `[' runtime ']` alone would fail
  // for a second reason (no mandatory `logging`) and pass this assertion even if
  // the padding were trimmed away.
  for (const padded of [
    [' logging'],
    ['logging '],
    [' runtime ', 'logging'],
    ['logging', ' runtime '],
  ]) {
    let paddedCapabilityError = null;
    try {
      await window.NekoMiniGame.connect({
        id: 'padded-capability-test',
        version: '1.0.0',
        requiredCapabilities: padded,
      }, { transport });
    } catch (error) { paddedCapabilityError = error; }
    assert(paddedCapabilityError?.code === 'invalid_manifest',
      `a whitespace-padded capability (${JSON.stringify(padded)}) was trimmed into a real one`);
  }

  // `required` entries are property NAMES and the schema types them as strings;
  // String(1) matched a property literally named "1".
  let numericRequiredError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'numeric-required-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      contracts: {
        events: {
          'numeric-required': {
            type: 'object',
            properties: { 1: { type: 'integer' } },
            required: [1],
          },
        },
      },
    }, { transport });
  } catch (error) { numericRequiredError = error; }
  assert(numericRequiredError?.code === 'invalid_manifest',
    'a non-string required entry was coerced into a matching property name');

  // `String(true)` is 'true', which matches the score-field pattern, so a boolean
  // silently became a board keyed on a field no entry will ever carry.
  let booleanScoreFieldError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'boolean-scorefield-test',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      optionalCapabilities: ['leaderboard-local'],
      leaderboards: {
        main: { scoreField: true, order: 'descending', maxEntries: 3, retention: 'recent' },
      },
    }, { transport });
  } catch (error) { booleanScoreFieldError = error; }
  assert(booleanScoreFieldError?.code === 'invalid_manifest',
    'a boolean scoreField was coerced into the field name "true"');

  // manifest.version is bounded at 64 in the schema, where JSON Schema counts
  // CODE POINTS; version.length charged two per astral character.
  const astralVersion = `1.0-${'🎮'.repeat(31)}`;
  assert(astralVersion.length > 64 && [...astralVersion].length <= 64,
    'the astral version fixture no longer straddles the UTF-16 and code-point bounds');
  const astralVersionGame = await window.NekoMiniGame.connect({
    id: 'astral-version-test',
    version: astralVersion,
    requiredCapabilities: ['logging'],
  }, { transport });
  assert(astralVersionGame.manifest.version === astralVersion,
    'a 35-code-point version was rejected by a 64-code-point bound');
  astralVersionGame.dispose();

  // Declared property names are bounded at 64 in the published schema, where
  // JSON Schema counts CODE POINTS. `name.length` charged two per astral
  // character, so the runtime was stricter than its own published contract.
  const astralName = 'gg' + '🎮'.repeat(32);
  assert(astralName.length > 64 && [...astralName].length <= 64,
    'the astral fixture no longer straddles the UTF-16 and code-point bounds');
  const astralGame = await window.NekoMiniGame.connect({
    id: 'astral-name-test',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
    contracts: {
      events: {
        'astral-event': {
          type: 'object',
          properties: { [astralName]: { type: 'integer' } },
          required: [astralName],
        },
      },
    },
  }, { transport });
  assert(astralGame.manifest.contracts.events['astral-event'].properties[astralName],
    'a 34-code-point property name was rejected by a 64-code-point bound');
  astralGame.dispose();

  // The manifest rule "memory/context/server-leaderboard/voice-input need
  // runtime" is checked against what the manifest REQUESTS. A host may
  // legitimately withhold an OPTIONAL runtime, and the dependent grant is then
  // permanently unusable -- every one of those calls needs an active route the
  // game can never start.
  // The fixture transport implements neither memory method, so `memory` would
  // never be granted here for an unrelated reason. Give the probe transports the
  // two methods so the grant is genuinely available, and the difference between
  // the two probes really is whether runtime was granted.
  const memoryCapableTransport = {
    ...transport,
    configureGameMemoryConsent: async () => ({ ok: true }),
    submitGameMemory: async () => ({ ok: true }),
  };
  const withheldRuntimeTransport = {
    ...memoryCapableTransport,
    async connectGame(request) {
      const base = await transport.connectGame(request);
      return {
        ...base,
        grantedCapabilities: base.grantedCapabilities.filter((name) => name !== 'runtime'),
      };
    },
  };
  let withheldRequiredError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'runtime-withheld-required',
      version: '1.0.0',
      requiredCapabilities: ['logging', 'memory'],
      optionalCapabilities: ['runtime'],
    }, { transport: withheldRuntimeTransport });
  } catch (error) { withheldRequiredError = error; }
  assert(withheldRequiredError?.code === 'capability_unavailable',
    'a required runtime-dependent capability connected without runtime');
  // Optional instead of required: the connection stands, but the unusable grant
  // must not be reported as granted.
  const withheldOptionalClient = await window.NekoMiniGame.connect({
    id: 'runtime-withheld-optional',
    version: '1.0.0',
    requiredCapabilities: ['logging'],
    optionalCapabilities: ['runtime', 'memory'],
  }, { transport: withheldRuntimeTransport });
  assert(!withheldOptionalClient.capabilities.has('runtime'),
    'the withheld-runtime probe did not actually withhold runtime');
  assert(!withheldOptionalClient.capabilities.has('memory'),
    'memory was reported as granted while runtime was withheld');
  withheldOptionalClient.dispose();
  // Contracts are not capabilities and cannot be dropped, so they fail instead.
  let withheldContractError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'runtime-withheld-contracts',
      version: '1.0.0',
      requiredCapabilities: ['logging'],
      optionalCapabilities: ['runtime'],
      contracts: { events: { 'round-started': { type: 'object' } } },
    }, { transport: withheldRuntimeTransport });
  } catch (error) { withheldContractError = error; }
  assert(withheldContractError?.code === 'capability_unavailable',
    'declared contracts connected without the runtime that carries them');
  // Control: the SAME manifest with runtime granted keeps its memory grant, so
  // none of the three assertions above can be satisfied by an implementation
  // that simply stopped granting memory.
  const grantedRuntimeClient = await window.NekoMiniGame.connect({
    id: 'runtime-granted-control',
    version: '1.0.0',
    requiredCapabilities: ['logging'],
    optionalCapabilities: ['runtime', 'memory'],
  }, { transport: memoryCapableTransport });
  assert(grantedRuntimeClient.capabilities.has('runtime')
    && grantedRuntimeClient.capabilities.has('memory'),
  'memory stopped being granted even when runtime was');
  grantedRuntimeClient.dispose();

  process.stdout.write('mini-game SDK runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
