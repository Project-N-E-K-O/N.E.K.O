const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertDeepEqual(actual, expected, message) {
  const actualJson = JSON.stringify(actual);
  const expectedJson = JSON.stringify(expected);
  if (actualJson !== expectedJson) {
    throw new Error(`${message}: expected ${expectedJson}, got ${actualJson}`);
  }
}

async function waitFor(predicate, message, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(message);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function storageResult(value, found = true) {
  return {
    ok: true,
    data: {
      ok: true,
      found,
      value,
    },
  };
}

function storedResult() {
  return {
    ok: true,
    data: {
      ok: true,
      stored: true,
    },
  };
}

function sampleDrawingPlan(accent = '#f4cf45') {
  return {
    version: 1,
    width: 800,
    height: 600,
    background: '#fffdfa',
    elements: [
      {
        type: 'ellipse',
        cx: 400,
        cy: 310,
        rx: 190,
        ry: 125,
        fill: accent,
        stroke: '#2f3b45',
        stroke_width: 10,
      },
      {
        type: 'polyline',
        points: [[270, 310], [345, 365], [455, 365], [530, 310]],
        fill: 'none',
        stroke: '#2f3b45',
        stroke_width: 8,
        line_cap: 'round',
        line_join: 'round',
      },
    ],
  };
}

function makeStorageClient(storage, enabled = true) {
  return {
    disposed: false,
    capabilities: {
      has(name) { return enabled && name === 'storage'; },
    },
    storage,
  };
}

function loadHarness() {
  const sourcePath = path.resolve(
    __dirname,
    '../../static/game/games/drawing_guess/drawing-guess.js',
  );
  const source = fs.readFileSync(sourcePath, 'utf8');
  const closingMarker = '\n})();';
  const closingIndex = source.lastIndexOf(closingMarker);
  assert(closingIndex >= 0, 'drawing-guess IIFE closing marker must exist');

  let localStorageReads = 0;
  const createdCanvases = [];
  let canvasDataUrlFactory = null;
  function makeCanvas() {
    const operations = [];
    const context = { operations };
    [
      'save', 'restore', 'beginPath', 'closePath', 'fill', 'stroke',
      'clearRect', 'fillRect', 'moveTo', 'lineTo', 'quadraticCurveTo',
      'arc', 'ellipse', 'rect', 'drawImage', 'setTransform',
    ].forEach((name) => {
      context[name] = (...args) => { operations.push({ name, args }); };
    });
    const canvas = {
      tagName: 'CANVAS',
      width: 0,
      height: 0,
      className: '',
      dataset: {},
      style: { setProperty() {}, removeProperty() {} },
      classList: { add() {}, remove() {}, toggle() {} },
      setAttribute() {},
      addEventListener() {},
      getContext(kind) { return kind === '2d' ? context : null; },
      toDataURL(type, quality) {
        operations.push({ name: 'toDataURL', args: [type, quality] });
        const fallback = `data:${type || 'image/png'};base64,${this.width}x${this.height}`;
        return canvasDataUrlFactory
          ? canvasDataUrlFactory({ canvas: this, type: type || 'image/png', quality, fallback })
          : fallback;
      },
      __context: context,
    };
    createdCanvases.push(canvas);
    return canvas;
  }
  function escapeXml(value) {
    return String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  }
  function serializeSvgNode(node) {
    const attrs = Object.entries(node.__attrs || {})
      .map(([key, value]) => ` ${key}="${escapeXml(value)}"`).join('');
    const children = (node.children || []).map(serializeSvgNode).join('');
    return children
      ? `<${node.tagName}${attrs}>${children}</${node.tagName}>`
      : `<${node.tagName}${attrs}/>`;
  }
  function makeSvgNode(tagName) {
    return {
      tagName,
      __attrs: {},
      children: [],
      setAttribute(key, value) { this.__attrs[String(key)] = String(value); },
      appendChild(child) { this.children.push(child); return child; },
      get outerHTML() { return serializeSvgNode(this); },
    };
  }
  const sandbox = {
    console,
    Promise,
    Set,
    Map,
    WeakMap,
    Date,
    Math,
    JSON,
    Number,
    String,
    Object,
    Array,
    RegExp,
    Error,
    TypeError,
    AbortController,
    Path2D: class {
      constructor(d) { this.d = d; }
    },
    XMLSerializer: class {
      serializeToString(node) { return serializeSvgNode(node); }
    },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame(callback) { return setTimeout(callback, 0); },
    cancelAnimationFrame(timer) { clearTimeout(timer); },
    document: {
      readyState: 'loading',
      addEventListener() {},
      getElementById() { return null; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      createElement(tagName) {
        if (String(tagName).toLowerCase() === 'canvas') return makeCanvas();
        return {
          addEventListener() {},
          appendChild() {},
          classList: { add() {}, remove() {}, toggle() {} },
          dataset: {},
          style: { setProperty() {} },
        };
      },
      createElementNS(_namespace, tagName) { return makeSvgNode(String(tagName)); },
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.__DRAWING_GUESS_BOOT__ = {};
  Object.defineProperty(sandbox, 'localStorage', {
    configurable: true,
    get() {
      localStorageReads += 1;
      throw new Error('raw_local_storage_access');
    },
  });

  const testExport = `
  window.__DRAWING_GUESS_PREFERENCE_TEST__ = {
    state: state,
    ensureSdkPreferenceChannels: ensureSdkPreferenceChannels,
    queueSdkPreferenceWrite: queueSdkPreferenceWrite,
    flushSdkPreferenceChannel: flushSdkPreferenceChannel,
    hydrateSdkPreferenceChannel: hydrateSdkPreferenceChannel,
    hydrateSdkPreferences: hydrateSdkPreferences,
    setPreferenceWriteRetryDelay: function (value) {
      SDK_PREFERENCE_WRITE_RETRY_DELAY_MS = Number(value) || 1;
    },
    saveModelViewSettings: saveModelViewSettings,
    saveColorHistory: saveColorHistory,
    configureSdkMemoryConsent: configureSdkMemoryConsent,
    normalizeAiDrawingPlan: normalizeAiDrawingPlan,
    renderAiDrawingPlanToCanvas: renderAiDrawingPlanToCanvas,
    captureAiDrawingReviewImage: captureAiDrawingReviewImage,
    aiDrawingPlanToSvg: aiDrawingPlanToSvg,
    normalizeAiDrawingSvg: normalizeAiDrawingSvg,
    fitAiDrawingSvgToContent: fitAiDrawingSvgToContent,
    canvasDisplayPixelBounds: canvasDisplayPixelBounds,
    floodFillPixelBuffer: floodFillPixelBuffer,
    prepareAiDrawing: prepareAiDrawing,
    captureUserCanvasPng: captureUserCanvasPng,
    captureVisionCommandImage: captureVisionCommandImage,
    postVisionGuess: postVisionGuess,
    submitFeedbackInput: submitFeedbackInput,
    submitDrawing: submitDrawing,
    chooseUserDrawWord: chooseUserDrawWord,
    triggerSupplementGuess: triggerSupplementGuess,
    triggerRandomAiGuess: triggerRandomAiGuess,
    handleAiGuessTimeout: handleAiGuessTimeout,
    stopAiGuessSchedule: stopAiGuessSchedule,
    flushDeferredAiGuessWork: flushDeferredAiGuessWork,
    settleAiGuessTimeout: settleAiGuessTimeout,
    requestGuessTimeout: requestGuessTimeout,
    submitUserGuess: submitUserGuess,
    submitGameChat: submitGameChat,
    addNekoMessage: addNekoMessage,
    logSdkBestEffort: logSdkBestEffort,
    currentLanguage: currentLanguage,
    submitPlayerText: submitPlayerText,
    handleSdkVoiceState: handleSdkVoiceState,
    handleSpeechPlaybackState: handleSpeechPlaybackState,
    handleSdkPageExit: handleSdkPageExit,
    querySdkVoiceRouteState: querySdkVoiceRouteState,
    stopSdkVoiceBestEffort: stopSdkVoiceBestEffort,
    handleVoiceRouteButton: handleVoiceRouteButton,
    cleanupRouteResources: cleanupRouteResources,
    startRoute: startRoute,
    startRound: startRound,
    finishGame: finishGame,
    updateControls: updateControls,
    installRoundLifecycleHarness: function (events, commandHandler) {
      function control() {
        return {
          hidden: false,
          disabled: false,
          textContent: '',
          setAttribute: function () {}
        };
      }
      els = {
        characterName: control(),
        sessionId: control(),
        memoryState: control(),
        doneButton: control(),
        nextRoundButton: control(),
        endButton: control(),
        clearCanvasButton: control(),
        tutorialOverlay: { hidden: true },
        chatSubmit: control(),
        chatInput: control(),
        undoTool: control(),
        redoTool: control(),
        voiceRouteButton: null,
        sizePreview: null,
        exitConfirm: null,
        exitReopenButton: null,
        ctx: null
      };
      clearNekoVoiceQueue = function () {};
      hideExitReopenButton = function () {};
      hideExitConfirm = function () {};
      resetCanvas = function () {};
      pushCanvasContextForRoute = function () {};
      showPlaceholder = function () {};
      setBadge = function () {};
      scheduleAiDrawingPlaceholderHint = function () {};
      renderFinalSummary = function () {
        state.phase = 'final_summary';
        events.push('final_summary');
      };
      showExitConfirm = function () { events.push('exit_confirm'); };
      executeRoundCommand = function (command, payload, timeoutMs) {
        return commandHandler(command, payload, timeoutMs);
      };
      return els;
    },
    installCanvasForCapture: function (canvas) {
      els.canvas = canvas;
    },
    setAiGuessTimeoutBusyMaxPolls: function (value) {
      AI_GUESS_TIMEOUT_BUSY_MAX_POLLS = Math.max(0, Number(value) || 0);
    },
    setAiGuessTimeoutBusyRetryTiming: function (windowMs, delayMs) {
      AI_GUESS_TIMEOUT_BUSY_RETRY_WINDOW_MS = Math.max(1, Number(windowMs) || 1);
      AI_GUESS_TIMEOUT_BUSY_RETRY_DELAY_MS = Math.max(1, Number(delayMs) || 1);
    },
    setAiGuessTimeoutRetryBaseDelay: function (value) {
      AI_GUESS_TIMEOUT_RETRY_BASE_DELAY_MS = Math.max(1, Number(value) || 1);
    },
    setGuessTimeoutRetryDelays: function (baseValue, maxValue) {
      GUESS_TIMEOUT_RETRY_BASE_DELAY_MS = Math.max(1, Number(baseValue) || 1);
      GUESS_TIMEOUT_RETRY_MAX_DELAY_MS = Math.max(
        GUESS_TIMEOUT_RETRY_BASE_DELAY_MS,
        Number(maxValue) || GUESS_TIMEOUT_RETRY_BASE_DELAY_MS
      );
    },
    installRoundCommandSpies: function (handler, events) {
      executeRoundCommand = function (command, payload, timeoutMs) {
        events.commands.push({ command: command, payload: payload, timeoutMs: timeoutMs });
        return Promise.resolve().then(function () {
          return handler(command, payload, timeoutMs);
        });
      };
      addMessage = function (key, fallback, params) {
        events.messages.push({ key: key, fallback: fallback, params: params });
      };
      addNekoMessage = function (text) { events.nekoMessages.push(text); };
      addEventMessage = function () {};
      addAiGuessOutcomeMessage = function () {};
      stopThinkingEventMessage = function () {};
      startThinkingEventMessage = function () {};
      startCountdown = function () {};
      flushDeferredAiGuessWork = function () {};
      scheduleNextRandomAiGuess = function () {};
      setChatPlaceholder = function () {};
      setPhase = function (phase) {
        state.phase = phase;
        events.phases.push(phase);
      };
      renderSummary = function (response) {
        state.aiGuessTimeoutSettling = false;
        state.phase = 'summary';
        events.summaries.push(response);
      };
      prepareUserDrawing = function (options, seconds) {
        state.phase = 'drawing_pick';
        if (events.userDrawPreparations) {
          events.userDrawPreparations.push({ options: options, seconds: seconds });
        }
      };
      updateControls = function () {};
    },
    installWordChoiceHarness: function (handler, events) {
      var classes = new Set(['dg-draw-pick-ready']);
      var reveal = { textContent: '' };
      var buttons = [];
      els.drawPick = {
        classList: {
          contains: function (name) { return classes.has(name); },
          add: function () {
            Array.prototype.slice.call(arguments).forEach(function (name) { classes.add(name); });
          },
          remove: function () {
            Array.prototype.slice.call(arguments).forEach(function (name) { classes.delete(name); });
          }
        },
        querySelectorAll: function () { return buttons; },
        querySelector: function () { return reveal; }
      };
      executeRoundCommand = function (command, payload, timeoutMs) {
        events.commands.push({ command: command, payload: payload, timeoutMs: timeoutMs });
        return Promise.resolve(handler(command, payload, timeoutMs));
      };
      renderDrawPickOptions = function () { events.restored += 1; };
      beginUserDrawing = function (answer, seconds) {
        events.transitions.push({ answer: answer, seconds: seconds });
        state.phase = 'user_drawing';
      };
      addMessage = function () { events.failures += 1; };
      updateControls = function () {};
    },
    installNekoMessageSpies: function (events) {
      addMessage = function (_key, text, _params, className) {
        events.push({ kind: 'bubble', text: text, className: className });
      };
      enqueueNekoVoice = function (text) { events.push({ kind: 'voice', text: text }); };
      pulseModelMood = function (mood) { events.push({ kind: 'mood', mood: mood }); };
    },
    installPlayerTextSpies: function (handler) {
      addUserMessage = function () {};
      submitUserGuess = function (value, metadata) { return handler('user_guessing', value, metadata); };
      submitGameChat = function (value, options) { return handler('game_chat', value, options); };
      submitFeedbackInput = function (value, metadata) { return handler('feedback', value, metadata); };
    },
    installPageExitCleanupSpy: function (events) {
      cleanupRouteResources = function () { events.push('cleanup'); };
    },
    installVoiceUiSpy: function (events) {
      els.chatMessages = {};
      addEventMessage = function (key) {
        if (events) events.push(key);
      };
      updateControls = function () {};
    },
    installRouteUiSpies: function () {
      setStatus = function () {};
      addMessage = function () {};
      stopThinkingEventMessage = function () {};
      updateControls = function () {};
    },
    installAiDrawingFitSpies: function (stage, metrics) {
      els.aiDrawing = stage;
      measureSvgContentMetrics = function () { return metrics; };
    },
    installLocaleUiSpies: function () {
      var calls = { updateControls: 0, setPhase: 0, syncBrushToolButton: 0 };
      updateControls = function () { calls.updateControls += 1; };
      setPhase = function () { calls.setPhase += 1; };
      syncBrushToolButton = function () { calls.syncBrushToolButton += 1; };
      return calls;
    }
  };
`;
  const instrumented = source.slice(0, closingIndex) + testExport + source.slice(closingIndex);
  vm.runInNewContext(instrumented, sandbox, {
    filename: sourcePath,
    timeout: 5000,
  });

  return {
    api: sandbox.__DRAWING_GUESS_PREFERENCE_TEST__,
    source,
    sandbox,
    createdCanvases,
    setCanvasDataUrlFactory(factory) { canvasDataUrlFactory = factory; },
    localStorageReads: () => localStorageReads,
  };
}

async function testEndWaitsForRoundSessionCreation() {
  const harness = loadHarness();
  const api = harness.api;
  const events = [];
  const roundStart = deferred();
  let aiDrawTimeoutMs = null;
  const controls = api.installRoundLifecycleHarness(events, (command, _payload, timeoutMs) => {
    if (command === 'round:start') return roundStart.promise;
    if (command === 'round:ai-draw') {
      aiDrawTimeoutMs = timeoutMs;
      return Promise.resolve({ ok: true, skipped: true, reason: 'not_ai_drawing' });
    }
    throw new Error(`unexpected round command: ${command}`);
  });
  api.state.lanlanName = 'SDK Neko';
  api.state.sessionId = 'drawing-round-readiness';
  api.state.routeActive = true;
  api.state.routeEnding = false;
  api.state.phase = 'tutorial';

  const pendingStart = api.startRound();
  assertEqual(api.state.phase, 'loading_round', 'round start should enter its loading phase');
  assertEqual(api.state.roundSessionReady, false, 'round session must stay unavailable while /round/start is pending');
  assertEqual(controls.endButton.disabled, true, 'End must stay disabled before the backend round exists');
  assertEqual(api.finishGame(), false, 'the finish guard must reject a programmatic early End');
  assertDeepEqual(events, [], 'an early End must not open an unusable final summary');

  roundStart.resolve({ ok: true });
  await pendingStart;
  assertEqual(aiDrawTimeoutMs, 90000,
    'AI drawing did not use the full registered route budget');
  assertEqual(api.state.roundSessionReady, true, 'a successful /round/start should unlock End');
  assertEqual(controls.endButton.disabled, false, 'End should be enabled once the backend round exists');
  assertEqual(api.finishGame(), true, 'finish should proceed after round creation');
  assertDeepEqual(events, ['final_summary', 'exit_confirm'], 'a valid End should open the final summary and exit confirmation');

  api.cleanupRouteResources();
  api.updateControls();
  assertEqual(api.state.roundSessionReady, false, 'route cleanup must retire round readiness');
  assertEqual(controls.endButton.disabled, true, 'End must lock again after route cleanup');
}

async function testWordChoiceRecoversCommittedBackendTransition() {
  const harness = loadHarness();
  const api = harness.api;
  const events = { commands: [], transitions: [], restored: 0, failures: 0 };
  api.installWordChoiceHarness((command) => {
    if (command !== 'round:choose-word') throw new Error(`unexpected command: ${command}`);
    return {
      ok: false,
      reason: 'not_word_picking',
      state: {
        phase: 'user_drawing',
        user_draw_answer: { id: 'cat', label: 'Cat' },
        timers: { draw_seconds: 137 },
      },
    };
  }, events);
  api.state.phase = 'drawing_pick';
  api.state.roundFlowToken = 5;
  api.state.activeRoundToken = 5;
  api.state.drawPickChoosing = false;
  api.state.drawPickOptions = [{ id: 'cat', label: 'Cat' }];

  await api.chooseUserDrawWord('cat');

  assertEqual(events.commands.length, 1, 'word selection did not use the SDK command');
  assertEqual(events.commands[0].timeoutMs, 10000, 'word selection lost its bounded request timeout');
  assertEqual(events.transitions.length, 1,
    'the committed backend transition did not recover the drawing phase');
  assertEqual(events.transitions[0].answer.id, 'cat',
    'word-selection recovery did not use the backend-authoritative answer');
  assertEqual(events.transitions[0].seconds, 137,
    'word-selection recovery did not use the backend-authoritative draw duration');
  assertEqual(events.restored, 0, 'the stale word picker was restored after successful recovery');
  assertEqual(events.failures, 0, 'successful recovery displayed an input failure');
}

async function testLateRoundStartCannotRestoreEndAfterCleanup() {
  const harness = loadHarness();
  const api = harness.api;
  const roundStart = deferred();
  const controls = api.installRoundLifecycleHarness([], (command) => {
    if (command === 'round:start') return roundStart.promise;
    throw new Error(`stale flow unexpectedly reached: ${command}`);
  });
  api.state.lanlanName = 'SDK Neko';
  api.state.sessionId = 'drawing-stale-round-start';
  api.state.routeActive = true;

  const pendingStart = api.startRound();
  api.state.routeActive = false;
  api.cleanupRouteResources();
  api.updateControls();
  roundStart.resolve({ ok: true });
  await pendingStart;

  assertEqual(api.state.roundSessionReady, false, 'a stale /round/start success must not restore readiness');
  assertEqual(controls.endButton.disabled, true, 'a stale /round/start success must not unlock End');
}

async function testLateHydrationKeepsLocalSideAndColorChanges() {
  const harness = loadHarness();
  const api = harness.api;
  const pendingReads = new Map();
  const writes = [];
  const storage = {
    get(key) {
      const read = deferred();
      pendingReads.set(key, read);
      return read.promise;
    },
    set(key, value) {
      writes.push({ key, value });
      return Promise.resolve(storedResult());
    },
  };
  const client = makeStorageClient(storage);
  api.state.sdkClient = client;

  const hydration = api.hydrateSdkPreferences(client);
  assertEqual(pendingReads.size, 3, 'all preference channels should begin hydration');

  api.state.sideSplitRatio = 0.77;
  api.queueSdkPreferenceWrite('sideSplit');
  api.state.colorHistory = ['#123456', '#abcdef'];
  api.saveColorHistory();

  pendingReads.get('settings/model-views').resolve(storageResult(undefined, false));
  pendingReads.get('settings/side-split-ratio').resolve(storageResult(0.31));
  pendingReads.get('settings/color-history').resolve(storageResult(['#fedcba']));
  await hydration;

  assertEqual(api.state.sideSplitRatio, 0.77, 'late side split hydration must not overwrite a local edit');
  assertDeepEqual(
    api.state.colorHistory,
    ['#123456', '#abcdef'],
    'late color hydration must not overwrite a local edit',
  );
  const sideWrite = writes.find((entry) => entry.key === 'settings/side-split-ratio');
  const colorWrite = writes.find((entry) => entry.key === 'settings/color-history');
  assert(sideWrite, 'dirty side split should be persisted after hydration');
  assert(colorWrite, 'dirty color history should be persisted after hydration');
  assertEqual(sideWrite.value, 0.77, 'persisted side split should use the local value');
  assertDeepEqual(colorWrite.value, ['#123456', '#abcdef'], 'persisted colors should use local values');
}

async function testLateModelViewHydrationMergesWithLocalPriority() {
  const harness = loadHarness();
  const api = harness.api;
  const read = deferred();
  const writes = [];
  const client = makeStorageClient({
    get() { return read.promise; },
    set(key, value) {
      writes.push({ key, value });
      return Promise.resolve(storedResult());
    },
  });
  api.state.sdkClient = client;
  api.state.lanlanName = 'Local Neko';
  api.state.modelViewSettings = [];

  const channel = api.ensureSdkPreferenceChannels().modelViews;
  const hydration = api.hydrateSdkPreferenceChannel(client, channel);
  api.state.modelView = { scale: 245, x: 12, y: -8 };
  api.saveModelViewSettings();

  read.resolve(storageResult([
    { character: 'Local Neko', view: { scale: 90, x: 1, y: 2 } },
    { character: 'Remote Neko', view: { scale: 175, x: -4, y: 9 } },
  ]));
  await hydration;

  const local = api.state.modelViewSettings.find((entry) => entry.character === 'Local Neko');
  const remote = api.state.modelViewSettings.find((entry) => entry.character === 'Remote Neko');
  assertDeepEqual(local.view, { scale: 245, x: 12, y: -8 }, 'current local model view must win the merge');
  assertDeepEqual(remote.view, { scale: 175, x: -4, y: 9 }, 'other remote character views must be retained');
  assertDeepEqual(api.state.modelView, local.view, 'the active model view should remain the local value');
  assertEqual(writes.length, 1, 'the merged model-view snapshot should be persisted once');
  assertEqual(writes[0].value.length, 2, 'the persisted model-view snapshot should contain both characters');
}

async function testCommittedWriteWaitsForHydrationBeforePersisting() {
  const harness = loadHarness();
  const api = harness.api;
  const read = deferred();
  const writes = [];
  let backingValue = [
    { character: 'Remote Neko', view: { scale: 175, x: -4, y: 9 } },
  ];
  const client = makeStorageClient({
    get() { return read.promise; },
    set(key, value) {
      backingValue = value;
      writes.push({ key, value });
      return Promise.resolve(storedResult());
    },
  });
  api.state.sdkClient = client;
  api.state.lanlanName = 'Local Neko';
  api.state.modelViewSettings = [];

  const channel = api.ensureSdkPreferenceChannels().modelViews;
  const hydration = api.hydrateSdkPreferenceChannel(client, channel);
  api.state.modelView = { scale: 245, x: 12, y: -8 };
  api.saveModelViewSettings();

  await new Promise((resolve) => setTimeout(resolve, 0));
  assertEqual(writes.length, 0, 'an immediate commit must not write before initial hydration settles');

  read.resolve(storageResult(backingValue));
  await hydration;
  await new Promise((resolve) => setTimeout(resolve, 0));

  assertEqual(writes.length, 1, 'hydration settlement should release one merged write');
  assertDeepEqual(
    writes[0].value.map((entry) => entry.character),
    ['Local Neko', 'Remote Neko'],
    'the first write must preserve local priority and untouched remote characters',
  );
}

async function testFailedHydrationRetriesBeforeMergingAndWriting() {
  const harness = loadHarness();
  const api = harness.api;
  const writes = [];
  let reads = 0;
  let backingValue = [
    { character: 'Remote Neko', view: { scale: 175, x: -4, y: 9 } },
  ];
  const client = makeStorageClient({
    get() {
      reads += 1;
      if (reads === 1) return Promise.reject(new Error('temporary_read_failure'));
      return Promise.resolve(storageResult(backingValue));
    },
    set(key, value) {
      backingValue = value;
      writes.push({ key, value });
      return Promise.resolve(storedResult());
    },
  });
  api.state.sdkClient = client;
  api.state.lanlanName = 'Local Neko';
  api.state.modelViewSettings = [];

  const channel = api.ensureSdkPreferenceChannels().modelViews;
  const firstHydration = api.hydrateSdkPreferenceChannel(client, channel);
  api.state.modelView = { scale: 245, x: 12, y: -8 };
  api.saveModelViewSettings();

  assertEqual(await firstHydration, false, 'the failed read should remain an unhydrated result');
  assertEqual(writes.length, 0, 'a failed read must not be treated as an absent storage key');
  assertDeepEqual(
    backingValue.map((entry) => entry.character),
    ['Remote Neko'],
    'the failed read path must not overwrite the existing remote snapshot',
  );

  await new Promise((resolve) => setTimeout(resolve, 240));
  assertEqual(reads, 2, 'a failed hydration should perform one bounded retry');
  assertEqual(writes.length, 1, 'the successful retry should release one merged write');
  assertDeepEqual(
    writes[0].value.map((entry) => entry.character),
    ['Local Neko', 'Remote Neko'],
    'the retry must merge dirty local state with the authoritative remote snapshot',
  );
}

async function testPreferenceWritesAreSerializedAndCoalesceFinalSnapshot() {
  const harness = loadHarness();
  const api = harness.api;
  const writes = [];
  let activeWrites = 0;
  let maxActiveWrites = 0;
  const client = makeStorageClient({
    get() { return Promise.resolve(storageResult(undefined, false)); },
    set(key, value) {
      const completion = deferred();
      activeWrites += 1;
      maxActiveWrites = Math.max(maxActiveWrites, activeWrites);
      writes.push({ key, value, completion });
      return completion.promise.finally(() => { activeWrites -= 1; });
    },
  });
  api.state.sdkClient = client;
  const channel = api.ensureSdkPreferenceChannels().colorHistory;
  channel.hydrated = true;

  api.state.colorHistory = ['#111111'];
  api.saveColorHistory();
  assertEqual(writes.length, 1, 'the first dirty snapshot should start one write');

  api.state.colorHistory = ['#222222', '#111111'];
  api.saveColorHistory();
  api.state.colorHistory = ['#333333', '#222222', '#111111'];
  api.saveColorHistory();
  assertEqual(writes.length, 1, 'a write in flight must block overlapping storage.set calls');

  writes[0].completion.resolve(storedResult());
  await new Promise((resolve) => setTimeout(resolve, 0));
  assertEqual(writes.length, 2, 'a changed revision should trigger one follow-up write');
  assertEqual(maxActiveWrites, 1, 'preference writes must remain serialized');
  assertDeepEqual(
    writes[1].value,
    ['#333333', '#222222', '#111111'],
    'the follow-up write should persist the final coalesced snapshot',
  );

  writes[1].completion.resolve(storedResult());
  await new Promise((resolve) => setTimeout(resolve, 0));
  assertEqual(activeWrites, 0, 'the final write should settle cleanly');
  assertEqual(writes.length, 2, 'intermediate snapshots should not create extra writes');
}

async function testFailedPreferenceWriteRetriesWithoutAnotherEdit() {
  const failures = [
    function () { return Promise.reject(new Error('temporary_write_failure')); },
    function () { return Promise.resolve({ ok: true, data: { ok: false, stored: false } }); },
  ];
  for (const failFirstWrite of failures) {
    const harness = loadHarness();
    const api = harness.api;
    const writes = [];
    const client = makeStorageClient({
      get() { return Promise.resolve(storageResult(undefined, false)); },
      set(key, value) {
        writes.push({ key, value });
        return writes.length === 1 ? failFirstWrite() : Promise.resolve(storedResult());
      },
    });
    api.setPreferenceWriteRetryDelay(2);
    api.state.sdkClient = client;
    const channel = api.ensureSdkPreferenceChannels().colorHistory;
    channel.hydrated = true;
    api.state.colorHistory = ['#123456', '#abcdef'];

    api.saveColorHistory();
    await waitFor(
      () => writes.length === 2 && !channel.inFlight && channel.writeFailures === 0,
      'a transient write failure did not complete its retry',
    );

    assertEqual(writes.length, 2, 'a transient write failure should retry without another edit');
    assertDeepEqual(writes[1].value, ['#123456', '#abcdef'],
      'the retry should preserve the committed preference snapshot');
    assertEqual(channel.dirty, false, 'a successful retry should clear the dirty preference state');
    assertEqual(channel.writeFailures, 0, 'a successful retry should reset the failure budget');
  }
}

async function testPreferenceWriteRetriesAreBoundedAndClientScoped() {
  const boundedHarness = loadHarness();
  const boundedApi = boundedHarness.api;
  let attempts = 0;
  const boundedClient = makeStorageClient({
    get() { return Promise.resolve(storageResult(undefined, false)); },
    set() {
      attempts += 1;
      return Promise.reject(new Error('persistent_write_failure'));
    },
  });
  boundedApi.setPreferenceWriteRetryDelay(2);
  boundedApi.state.sdkClient = boundedClient;
  const boundedChannel = boundedApi.ensureSdkPreferenceChannels().colorHistory;
  boundedChannel.hydrated = true;
  boundedApi.state.colorHistory = ['#654321'];

  boundedApi.saveColorHistory();
  await waitFor(
    () => attempts === 3 && !boundedChannel.inFlight && !boundedChannel.writeRetryTimer,
    'persistent preference failures did not exhaust the bounded retry budget',
  );
  assertEqual(attempts, 3, 'a persistent write failure should consume only the bounded attempts');
  assertEqual(boundedChannel.writeFailures, 3,
    'a persistent write failure did not stop at the configured attempt budget');
  assertEqual(boundedChannel.dirty, true, 'an exhausted write must remain dirty in memory');
  assertEqual(boundedChannel.writeRetryTimer, null, 'an exhausted write must not leave a retry timer');

  const disposedHarness = loadHarness();
  const disposedApi = disposedHarness.api;
  let disposedAttempts = 0;
  const disposedClient = makeStorageClient({
    get() { return Promise.resolve(storageResult(undefined, false)); },
    set() {
      disposedAttempts += 1;
      return Promise.reject(new Error('write_failed_before_dispose'));
    },
  });
  disposedApi.setPreferenceWriteRetryDelay(100);
  disposedApi.state.sdkClient = disposedClient;
  const disposedChannel = disposedApi.ensureSdkPreferenceChannels().colorHistory;
  disposedChannel.hydrated = true;
  disposedApi.state.colorHistory = ['#abcdef'];

  disposedApi.saveColorHistory();
  await waitFor(
    () => Boolean(disposedChannel.writeRetryTimer),
    'the transient failure did not schedule a retry before the client was disposed',
  );
  assert(disposedChannel.writeRetryTimer,
    'the transient failure did not schedule a retry before the client was disposed');
  disposedClient.disposed = true;
  await waitFor(
    () => disposedChannel.writeRetryTimer === null,
    'the disposed client retry timer did not settle',
  );
  assertEqual(disposedAttempts, 1, 'a disposed SDK client performed a preference retry');
}

async function testUnavailableStorageNeverFallsBackToRawLocalStorage() {
  const harness = loadHarness();
  const api = harness.api;
  assert(!/\blocalStorage\b/.test(harness.source), 'game source must not contain a raw localStorage dependency');

  let storagePropertyReads = 0;
  const client = {
    disposed: false,
    capabilities: { has() { return false; } },
  };
  Object.defineProperty(client, 'storage', {
    get() {
      storagePropertyReads += 1;
      throw new Error('storage_capability_was_not_granted');
    },
  });
  api.state.sdkClient = client;

  const hydrated = await api.hydrateSdkPreferences(client);
  api.state.colorHistory = ['#445566'];
  api.saveColorHistory();
  const flushed = await api.flushSdkPreferenceChannel(api.ensureSdkPreferenceChannels().colorHistory);

  assertEqual(hydrated, false, 'hydration should be a no-op without storage capability');
  assertEqual(flushed, false, 'flush should be a no-op without storage capability');
  assertEqual(storagePropertyReads, 0, 'client.storage must not be touched without capability');
  assertEqual(harness.localStorageReads(), 0, 'raw localStorage must never be read');
}

async function testMemoryConsentUsesSdkAndRejectsLockedMismatch() {
  const enabledHarness = loadHarness();
  const enabledCalls = [];
  enabledHarness.api.state.memoryConsent = 'summary';
  const enabled = await enabledHarness.api.configureSdkMemoryConsent({
    memory: {
      consent: { locked: false, configured: false, enabled: false },
      configureConsent(value, options) {
        enabledCalls.push({ value, options });
        return Promise.resolve({ ok: true, data: { ok: true, enabled: value } });
      },
    },
  });
  assertEqual(enabled, true, 'summary consent should be accepted');
  assertEqual(enabledCalls.length, 1, 'summary consent should call the SDK once');
  assertEqual(enabledCalls[0].value, true, 'summary consent should configure true');

  const disabledHarness = loadHarness();
  const disabledCalls = [];
  disabledHarness.api.state.memoryConsent = 'none';
  const disabled = await disabledHarness.api.configureSdkMemoryConsent({
    memory: {
      consent: { locked: false, configured: false, enabled: false },
      configureConsent(value, options) {
        disabledCalls.push({ value, options });
        return Promise.resolve({ ok: true, data: { ok: true, enabled: value } });
      },
    },
  });
  assertEqual(disabled, true, 'none consent should be accepted');
  assertEqual(disabledCalls.length, 1, 'none consent should call the SDK once');
  assertEqual(disabledCalls[0].value, false, 'none consent should configure false');

  const lockedHarness = loadHarness();
  let lockedConfigureCalls = 0;
  lockedHarness.api.state.memoryConsent = 'summary';
  let lockedError = null;
  try {
    await lockedHarness.api.configureSdkMemoryConsent({
      memory: {
        consent: { locked: true, configured: true, enabled: false },
        configureConsent() {
          lockedConfigureCalls += 1;
          return Promise.resolve(storedResult());
        },
      },
    });
  } catch (error) {
    lockedError = error;
  }
  assert(lockedError, 'a locked consent mismatch must reject');
  assertEqual(lockedError.code, 'memory_consent_locked', 'locked mismatch should use a stable error code');
  assertEqual(lockedConfigureCalls, 0, 'locked mismatch must not call configureConsent');
}

async function testPlayerTextCommandsStaySerialized() {
  const harness = loadHarness();
  const api = harness.api;
  const first = deferred();
  const calls = [];
  api.installPlayerTextSpies((kind, value, metadata) => {
    calls.push({ kind, value, metadata });
    return value === 'first' ? first.promise : Promise.resolve();
  });
  api.state.routeActive = true;
  api.state.routeEnding = false;
  api.state.phase = 'user_guessing';

  assertEqual(api.submitPlayerText('first', { inputMetadata: { source: 'voice' } }), true,
    'the first player input should be accepted');
  assertEqual(api.submitPlayerText('second', { inputMetadata: { source: 'voice' } }), true,
    'the second player input should be queued');
  await Promise.resolve();
  await Promise.resolve();
  assertDeepEqual(calls.map((call) => call.value), ['first'],
    'a later voice transcript must not start while the first command is in flight');

  first.resolve();
  await api.state.playerTextChain;
  assertDeepEqual(calls.map((call) => call.value), ['first', 'second'],
    'queued player inputs must preserve recognition order');
}

async function testQueuedPlayerTextDoesNotCrossPhaseBoundary() {
  const harness = loadHarness();
  const api = harness.api;
  const first = deferred();
  const calls = [];
  api.installPlayerTextSpies((_kind, value) => {
    calls.push(value);
    return value === 'first' ? first.promise : Promise.resolve();
  });
  api.state.routeActive = true;
  api.state.routeEnding = false;
  api.state.phase = 'user_guessing';

  api.submitPlayerText('first');
  api.submitPlayerText('second');
  await Promise.resolve();
  await Promise.resolve();
  api.state.phase = 'ai_guess_feedback';
  first.resolve();
  await api.state.playerTextChain;

  assertDeepEqual(calls, ['first'],
    'queued text from an earlier phase must not be reinterpreted after the phase changes');
}

async function testVoiceStateCannotClearAnActiveControlRequest() {
  const harness = loadHarness();
  const api = harness.api;
  api.installVoiceUiSpy();
  api.state.voiceControlPending = true;

  api.handleSdkVoiceState({ active: true, reason: 'recognition_started' });

  assertEqual(api.state.voiceControlPending, true,
    'unsolicited recognition state must not clear the current toggle request fence');
}

async function testBackgroundVoiceQueryCannotClearANewerToggle() {
  const harness = loadHarness();
  const api = harness.api;
  const query = deferred();
  const client = {
    disposed: false,
    runtime: { state: 'running' },
    capabilities: { has(name) { return name === 'voice-input'; } },
    voice: { query() { return query.promise; } },
  };
  api.installVoiceUiSpy();
  api.state.sdkClient = client;
  api.state.routeActive = true;
  api.state.routeEnding = false;
  api.state.voiceControlRequestSequence = 3;
  const pendingQuery = api.querySdkVoiceRouteState(client);
  api.state.voiceControlRequestSequence = 4;
  api.state.voiceControlPending = true;
  api.state.voiceRouteActive = true;
  query.reject(new Error('query timeout'));
  await pendingQuery;

  assertEqual(api.state.voiceControlPending, true,
    'a stale background query must not clear the newer toggle pending fence');
  assertEqual(api.state.voiceRouteActive, true,
    'a stale background query must not overwrite the newer voice state');
}

async function testVoiceToggleUsesOfficialSdkControl() {
  const harness = loadHarness();
  const api = harness.api;
  const events = [];
  const toggleCalls = [];
  const client = {
    disposed: false,
    capabilities: { has(name) { return name === 'voice-input'; } },
    voice: {
      connected: true,
      toggle(options) {
        toggleCalls.push(options);
        return Promise.resolve({ ok: true, active: true });
      },
    },
  };
  api.installVoiceUiSpy(events);
  api.state.sdkClient = client;
  api.state.routeActive = true;
  api.state.routeEnding = false;

  api.handleVoiceRouteButton();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assertEqual(toggleCalls.length, 1, 'voice toggle did not use the SDK voice facade');
  assertEqual(toggleCalls[0].timeoutMs, 12000, 'voice toggle lost its bounded timeout');
  assertEqual(api.state.voiceRouteActive, true, 'successful SDK toggle did not update route voice state');
  assertEqual(api.state.voiceControlPending, false, 'successful SDK toggle did not release its request fence');
  assert(events.includes('drawingGuess.voice.connectedNotice'),
    'successful SDK toggle did not publish the connected notice');
}

async function testRouteStartQueriesVoiceWithoutTakingOverMicrophone() {
  const harness = loadHarness();
  const api = harness.api;
  let queryCalls = 0;
  const client = {
    disposed: false,
    runtime: {
      state: 'idle',
      session: { id: 'drawing-query-session', routeInstanceId: 'drawing-query-route' },
      start() {
        this.state = 'running';
        return Promise.resolve({ ok: true, data: { ok: true } });
      },
    },
    memory: {
      consent: { locked: true, configured: true, enabled: false },
    },
    capabilities: {
      granted: ['voice-input'],
      has(name) { return name === 'voice-input'; },
    },
    logger: {
      enableAfterRuntimeStart() { return Promise.resolve({ ok: false }); },
    },
    voice: {
      query(options) {
        queryCalls += 1;
        assertEqual(options.timeoutMs, 5000, 'route-start voice query lost its bounded timeout');
        return Promise.resolve({ ok: true, active: false });
      },
    },
  };
  api.installRouteUiSpies();
  api.state.lanlanName = 'SDK Neko';
  api.state.sessionId = 'drawing-query-session';
  api.state.sdkClient = client;

  assertEqual(await api.startRoute(), true, 'route start failed before querying official voice state');
  await Promise.resolve();
  assertEqual(queryCalls, 1, 'route start did not query the official voice owner');
}

async function testCanvasDrawingPlanIsBoundedRenderedAndSerializable() {
  const harness = loadHarness();
  const api = harness.api;
  const normalized = api.normalizeAiDrawingPlan(sampleDrawingPlan());

  assert(normalized, 'a valid backend drawing plan was rejected by the browser renderer');
  assertEqual(normalized.version, 1, 'the drawing plan version changed during normalization');
  assertEqual(normalized.width, 800, 'the drawing plan width changed during normalization');
  assertEqual(normalized.height, 600, 'the drawing plan height changed during normalization');
  assertEqual(normalized.elements.length, 2, 'valid drawing primitives were dropped');

  const canvas = harness.sandbox.document.createElement('canvas');
  assertEqual(api.renderAiDrawingPlanToCanvas(normalized, canvas), true,
    'the normalized plan did not render to local Canvas');
  assertEqual(canvas.width, 800, 'the local drawing canvas used the wrong width');
  assertEqual(canvas.height, 600, 'the local drawing canvas used the wrong height');
  const operationNames = canvas.__context.operations.map((operation) => operation.name);
  assert(operationNames.includes('clearRect') && operationNames.includes('fillRect'),
    'Canvas rendering did not paint an opaque local background');
  assert(operationNames.includes('ellipse') && operationNames.includes('moveTo')
    && operationNames.includes('lineTo'),
  'Canvas rendering did not execute the declared geometry');
  assert(operationNames.includes('fill') && operationNames.includes('stroke'),
    'Canvas rendering lost the declared paint operations');

  const svg = api.aiDrawingPlanToSvg(normalized);
  assert(svg.includes('<svg') && svg.includes('viewBox="0 0 800 600"'),
    'the plan did not produce the SVG compatibility artifact');
  assert(svg.includes('<ellipse') && svg.includes('<polyline'),
    'the SVG compatibility artifact dropped drawing primitives');
  assert(!/<(?:text|script|image|foreignObject)\b/i.test(svg),
    'the local SVG serializer emitted a disallowed semantic or executable element');
  assert(!/url\(|https?:|data:image/i.test(svg),
    'the local SVG serializer emitted an external resource');

  const semanticPlan = sampleDrawingPlan();
  semanticPlan.elements[0].text = 'PRIVATE_ANSWER';
  assertEqual(api.normalizeAiDrawingPlan(semanticPlan), null,
    'the browser accepted an undeclared semantic field in a drawing element');
  const nullablePlan = sampleDrawingPlan();
  nullablePlan.version = null;
  assertEqual(api.normalizeAiDrawingPlan(nullablePlan), null,
    'the browser treated an explicit null plan version as the canonical version');
  const aliasPlan = sampleDrawingPlan();
  aliasPlan.elements[0] = {
    type: 'rect', x: 200, y: 160, width: 400, height: 280, radius: 12,
    fill: '#f4cf45', stroke: '#2f3b45', stroke_width: 8,
  };
  assertEqual(api.normalizeAiDrawingPlan(aliasPlan), null,
    'the browser accepted a rect radius alias outside the backend schema');
  const outOfBoundsPlan = sampleDrawingPlan();
  outOfBoundsPlan.elements[0].cx = 790;
  assertEqual(api.normalizeAiDrawingPlan(outOfBoundsPlan), null,
    'the browser silently changed an out-of-bounds backend drawing plan');
}

async function testRawAiSvgFillsResponsiveStage() {
  const harness = loadHarness();
  const attrs = { viewBox: '0 0 800 600', width: '800', height: '600' };
  const svg = {
    style: {},
    getAttribute(name) { return attrs[name] || null; },
    setAttribute(name, value) { attrs[name] = String(value); },
    removeAttribute(name) { delete attrs[name]; },
  };
  harness.api.normalizeAiDrawingSvg(svg);
  assertEqual(attrs.preserveAspectRatio, 'none',
    'raw AI SVG kept aspect-ratio letterboxing instead of filling the drawing stage');
  assert(!Object.prototype.hasOwnProperty.call(attrs, 'width')
    && !Object.prototype.hasOwnProperty.call(attrs, 'height'),
  'raw AI SVG kept fixed dimensions that can diverge from the player canvas');

  harness.api.installAiDrawingFitSpies({
    getBoundingClientRect() {
      return { left: 0, top: 0, right: 1000, bottom: 500, width: 1000, height: 500 };
    },
  }, {
    bounds: { x1: 200, y1: 200, x2: 600, y2: 400 },
    centerX: 400,
    centerY: 300,
  });
  harness.api.fitAiDrawingSvgToContent(svg);
  const fittedViewBox = attrs.viewBox.split(/\s+/).map(Number);
  assert(Math.abs((fittedViewBox[2] / fittedViewBox[3]) - 2) < 0.001,
    'raw AI SVG content fitting ignored the responsive stage aspect ratio');
}

async function testBucketFillTreatsCanvasDisplayEdgeAsBoundary() {
  const harness = loadHarness();
  const width = 9;
  const height = 9;
  const pixels = new Uint8ClampedArray(width * height * 4);
  const barrier = { r: 36, g: 48, b: 58, a: 255 };
  const fill = { r: 244, g: 207, b: 69, a: 255 };
  const visibleBounds = { minX: 0, minY: 0, maxX: 8, maxY: 6 };
  function setTestPixel(x, y, color) {
    const index = (y * width + x) * 4;
    pixels[index] = color.r;
    pixels[index + 1] = color.g;
    pixels[index + 2] = color.b;
    pixels[index + 3] = color.a;
  }
  function testPixel(x, y) {
    const index = (y * width + x) * 4;
    return Array.from(pixels.slice(index, index + 4));
  }
  for (let y = 1; y < visibleBounds.maxY; y += 1) setTestPixel(4, y, barrier);

  assertEqual(harness.api.floodFillPixelBuffer(
    pixels, width, height, 1, 3, fill, visibleBounds,
  ), true,
    'bucket fill rejected an enclosed edge-bounded region');
  assertDeepEqual(testPixel(1, 3), [244, 207, 69, 255],
    'bucket fill did not color the selected side');
  assertDeepEqual(testPixel(1, 0), [244, 207, 69, 255],
    'bucket fill did not project the selected region onto the canvas edge');
  assertDeepEqual(testPixel(6, 3), [0, 0, 0, 0],
    'bucket fill escaped around a stroke ending at the canvas edge');
  assertDeepEqual(testPixel(6, 0), [0, 0, 0, 0],
    'the canvas edge connected two otherwise separate fill regions');
  assertDeepEqual(testPixel(4, 3), [36, 48, 58, 255],
    'bucket fill overwrote the brush boundary');
  assertDeepEqual(testPixel(1, 7), [0, 0, 0, 0],
    'bucket fill changed pixels clipped outside the visible canvas area');
  const pixelsBeforeHiddenStart = Array.from(pixels);
  assertEqual(harness.api.floodFillPixelBuffer(
    pixels, width, height, 1, 8, fill, visibleBounds,
  ), false, 'bucket fill accepted a start point outside the visible canvas area');
  assertDeepEqual(Array.from(pixels), pixelsBeforeHiddenStart,
    'an out-of-view bucket start changed the pixel buffer');
  assertEqual(harness.api.floodFillPixelBuffer(
    pixels, width, height, 1, 3, fill, null,
  ), false, 'bucket fill treated an explicitly invisible canvas as fully visible');
  assertDeepEqual(Array.from(pixels), pixelsBeforeHiddenStart,
    'an explicitly invisible canvas changed the pixel buffer');

  const mappedBounds = harness.api.canvasDisplayPixelBounds({
    width: 800,
    height: 600,
    getBoundingClientRect() {
      return { left: 0, top: -100, right: 800, bottom: 500, width: 800, height: 600 };
    },
  }, {
    getBoundingClientRect() {
      return { left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400 };
    },
  });
  assertDeepEqual(mappedBounds, { minX: 0, minY: 100, maxX: 799, maxY: 499 },
    'visible canvas clipping was not mapped back into the pixel buffer');

  const clippingAncestor = {
    parentElement: null,
    getBoundingClientRect() {
      return { left: 0, top: 0, right: 800, bottom: 300, width: 800, height: 300 };
    },
  };
  const stage = {
    parentElement: clippingAncestor,
    getBoundingClientRect() {
      return { left: 0, top: -100, right: 800, bottom: 400, width: 800, height: 500 };
    },
  };
  const clippedCanvas = {
    width: 800,
    height: 600,
    parentElement: stage,
    getBoundingClientRect() {
      return { left: 0, top: -100, right: 800, bottom: 500, width: 800, height: 600 };
    },
  };
  harness.sandbox.getComputedStyle = (element) => (element === clippingAncestor
    ? { overflow: 'hidden', overflowX: 'hidden', overflowY: 'hidden' }
    : { overflow: 'visible', overflowX: 'visible', overflowY: 'visible' });
  const ancestorClippedBounds = harness.api.canvasDisplayPixelBounds(clippedCanvas, stage);
  assertDeepEqual(ancestorClippedBounds, { minX: 0, minY: 100, maxX: 799, maxY: 399 },
    'an outer overflow clipping edge was not mapped back into the pixel buffer');

  harness.sandbox.visualViewport = {
    width: 800,
    height: 200,
    offsetLeft: 0,
    offsetTop: 50,
  };
  const viewportClippedBounds = harness.api.canvasDisplayPixelBounds(clippedCanvas, stage);
  assertDeepEqual(viewportClippedBounds, { minX: 0, minY: 150, maxX: 799, maxY: 349 },
    'the visual viewport edge was not mapped back into the pixel buffer');

  harness.sandbox.visualViewport = null;
  harness.sandbox.innerWidth = 2000;
  harness.sandbox.innerHeight = 2000;
  const yClip = {
    parentElement: null,
    __style: { overflow: 'visible', overflowX: 'visible', overflowY: 'hidden' },
    getBoundingClientRect() {
      return { left: -500, top: 0, right: 1000, bottom: 300, width: 1500, height: 300 };
    },
  };
  const visibleNarrowAncestor = {
    parentElement: yClip,
    __style: { overflow: 'visible', overflowX: 'visible', overflowY: 'visible' },
    getBoundingClientRect() {
      return { left: 250, top: 100, right: 300, bottom: 150, width: 50, height: 50 };
    },
  };
  const xClip = {
    parentElement: visibleNarrowAncestor,
    __style: { overflow: 'visible', overflowX: 'hidden', overflowY: 'visible' },
    getBoundingClientRect() {
      return { left: 0, top: -500, right: 600, bottom: 1000, width: 600, height: 1500 };
    },
  };
  const axisStage = {
    parentElement: xClip,
    getBoundingClientRect() {
      return { left: -100, top: -100, right: 700, bottom: 500, width: 800, height: 600 };
    },
  };
  const axisCanvas = {
    width: 800,
    height: 600,
    parentElement: axisStage,
    getBoundingClientRect() {
      return { left: -100, top: -100, right: 700, bottom: 500, width: 800, height: 600 };
    },
  };
  harness.sandbox.getComputedStyle = (element) => element.__style
    || { overflow: 'visible', overflowX: 'visible', overflowY: 'visible' };
  assertDeepEqual(
    harness.api.canvasDisplayPixelBounds(axisCanvas, axisStage),
    { minX: 100, minY: 100, maxX: 699, maxY: 399 },
    'axis-specific overflow clips or an overflow-visible ancestor were handled incorrectly',
  );

  const borderedClip = {
    parentElement: null,
    offsetWidth: 800,
    offsetHeight: 300,
    clientLeft: 1,
    clientTop: 1,
    clientWidth: 798,
    clientHeight: 298,
    __style: { overflow: 'hidden', overflowX: 'hidden', overflowY: 'hidden' },
    getBoundingClientRect() {
      return { left: 0, top: 0, right: 800, bottom: 300, width: 800, height: 300 };
    },
  };
  stage.parentElement = borderedClip;
  assertDeepEqual(
    harness.api.canvasDisplayPixelBounds(clippedCanvas, stage),
    { minX: 1, minY: 101, maxX: 798, maxY: 398 },
    'the overflow client box did not exclude the ancestor border pixels',
  );

  borderedClip.getBoundingClientRect = () => (
    { left: 0, top: 700, right: 800, bottom: 1000, width: 800, height: 300 }
  );
  assertEqual(harness.api.canvasDisplayPixelBounds(clippedCanvas, stage), null,
    'a fully clipped canvas failed open to its entire backing buffer');

  clippedCanvas.getBoundingClientRect = () => (
    { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }
  );
  assertEqual(harness.api.canvasDisplayPixelBounds(clippedCanvas, stage), null,
    'a zero-sized canvas failed open to its entire backing buffer');
}

async function testComplexDrawingPlanSupportsCurvesAndMoreDetail() {
  const harness = loadHarness();
  const api = harness.api;
  const complexPlan = sampleDrawingPlan();
  complexPlan.elements = [
    {
      type: 'path',
      d: 'M 150 320 C 210 90 590 90 650 320 Q 400 540 150 320 Z',
      fill: '#f4cf45',
      stroke: '#2f3b45',
      stroke_width: 8,
    },
  ].concat(Array.from({ length: 80 }, (_value, index) => ({
    type: 'circle',
    cx: 200 + (index % 20) * 20,
    cy: 200 + Math.floor(index / 20) * 20,
    r: 4,
    fill: '#ffffff',
    stroke: 'none',
    stroke_width: 1,
  })));

  const normalized = api.normalizeAiDrawingPlan(complexPlan);
  assert(normalized, 'a detailed plan above the old 70-element cap was rejected');
  assertEqual(normalized.elements.length, 81, 'the detailed plan lost drawing elements');
  assertEqual(normalized.elements[0].type, 'path', 'the curved path was dropped');

  const canvas = harness.sandbox.document.createElement('canvas');
  assertEqual(api.renderAiDrawingPlanToCanvas(normalized, canvas), true,
    'the curved drawing plan did not render to Canvas');
  const pathPaint = canvas.__context.operations.find((operation) => (
    (operation.name === 'fill' || operation.name === 'stroke')
      && operation.args[0] && operation.args[0].d
  ));
  assert(pathPaint && pathPaint.args[0].d.includes('C 210 90'),
    'Canvas rendering did not use the declared curved path');

  const svg = api.aiDrawingPlanToSvg(normalized);
  assert(svg.includes('<path') && svg.includes('C 210 90'),
    'the SVG compatibility artifact dropped the curved path');

  complexPlan.elements[0].d = 'M 20 20 L 40 40<script>';
  assertEqual(api.normalizeAiDrawingPlan(complexPlan), null,
    'the browser accepted executable markup inside path data');
}

async function testDrawingReviewCaptureIsLowResolutionOpaqueJpeg() {
  const harness = loadHarness();
  const image = harness.api.captureAiDrawingReviewImage(sampleDrawingPlan());

  assertEqual(image, 'data:image/jpeg;base64,384x288',
    'the visual review image was not the bounded low-resolution JPEG');
  assertEqual(harness.createdCanvases.length, 2,
    'drawing review should need one full-size source and one review canvas');
  const source = harness.createdCanvases[0];
  const review = harness.createdCanvases[1];
  assertEqual(source.width, 800, 'the review source did not render the canonical plan width');
  assertEqual(source.height, 600, 'the review source did not render the canonical plan height');
  assertEqual(review.width, 384, 'the review image was not downsampled to the expected width');
  assertEqual(review.height, 288, 'the review image was not downsampled to the expected height');
  const reviewOperations = review.__context.operations;
  assert(reviewOperations.some((operation) => operation.name === 'fillRect'),
    'the JPEG review canvas was not given an opaque background');
  const drawImage = reviewOperations.find((operation) => operation.name === 'drawImage');
  assert(drawImage && drawImage.args.slice(1).join(',') === '0,0,384,288',
    'the full drawing was not scaled into the bounded review image');
  const encoded = reviewOperations.find((operation) => operation.name === 'toDataURL');
  assertDeepEqual(encoded.args, ['image/jpeg', 0.78],
    'the visual review image used an unexpected encoding or quality');
}

async function testVisionCommandsUseBoundedSnapshotsAndPreserveFullPng() {
  const harness = loadHarness();
  const api = harness.api;
  const sourceCanvas = harness.sandbox.document.createElement('canvas');
  sourceCanvas.width = 800;
  sourceCanvas.height = 600;
  api.installCanvasForCapture(sourceCanvas);

  const oversizedPng = `data:image/png;base64,${'x'.repeat(1800000)}`;
  harness.setCanvasDataUrlFactory(({ type, fallback }) => (
    type === 'image/png' ? oversizedPng : fallback
  ));

  const fullPng = api.captureUserCanvasPng();
  const commandImage = api.captureVisionCommandImage();
  assertEqual(fullPng, oversizedPng, 'the full-resolution PNG capture was unexpectedly replaced');
  assertEqual(commandImage, 'data:image/jpeg;base64,480x360',
    'the vision command image was not a downscaled JPEG');
  assert(commandImage.length < 1800000, 'the bounded vision image still exceeded the command contract');

  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  api.installRoundCommandSpies(() => ({ ok: false, reason: 'test_response' }), events);
  api.state.phase = 'user_drawing';
  api.state.hasDrawn = true;
  api.state.roundFlowToken = 12;
  api.state.activeRoundToken = 12;

  api.submitDrawing(true);
  await waitFor(() => events.commands.length === 1 && !api.state.aiGuessInFlight,
    'the initial vision guess did not finish');

  api.state.phase = 'ai_guess_feedback';
  api.triggerSupplementGuess(false);
  await waitFor(() => events.commands.length === 2 && !api.state.aiGuessInFlight,
    'the supplemental vision guess did not finish');

  api.state.phase = 'ai_guess_feedback';
  api.triggerRandomAiGuess();
  await waitFor(() => events.commands.length === 3 && !api.state.aiGuessInFlight,
    'the automatic vision guess did not finish');

  api.state.phase = 'ai_guess_feedback';
  await api.submitFeedbackInput('another hint', { request_id: 'bounded-feedback' });

  assertEqual(events.commands.length, 4, 'not every vision-bearing command was exercised');
  events.commands.forEach((call) => {
    assertEqual(call.payload.image_data_url, 'data:image/jpeg;base64,480x360',
      `${call.command} sent an unbounded canvas image`);
    assertEqual(call.timeoutMs, 350000,
      `${call.command} did not reserve time for final guess post-processing`);
  });
  assertEqual(api.state.userPng, oversizedPng,
    'network image bounding overwrote the full PNG used by summaries and downloads');
}

async function testDeferredVisionSnapshotsPreserveTriggerImage() {
  const harness = loadHarness();
  const api = harness.api;
  const sourceCanvas = harness.sandbox.document.createElement('canvas');
  sourceCanvas.width = 800;
  sourceCanvas.height = 600;
  api.installCanvasForCapture(sourceCanvas);
  let jpegMarker = 'initial';
  harness.setCanvasDataUrlFactory(({ type }) => (
    type === 'image/png'
      ? 'data:image/png;base64,full-resolution'
      : `data:image/jpeg;base64,${jpegMarker}`
  ));
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  api.installRoundCommandSpies(() => ({ ok: false, reason: 'test_response' }), events);
  api.state.phase = 'ai_guess_feedback';
  api.state.roundFlowToken = 15;
  api.state.activeRoundToken = 15;

  api.state.chatInFlight = true;
  jpegMarker = 'supplement-A';
  api.triggerSupplementGuess(false);
  assertEqual(api.state.pendingSupplementImage, 'data:image/jpeg;base64,supplement-A',
    'the supplemental trigger did not queue its bounded snapshot');
  jpegMarker = 'supplement-B';
  api.state.chatInFlight = false;
  api.flushDeferredAiGuessWork();
  await waitFor(() => events.commands.length === 1 && !api.state.aiGuessInFlight,
    'the deferred supplemental guess did not finish');
  assertEqual(events.commands[0].payload.image_data_url, 'data:image/jpeg;base64,supplement-A',
    'the deferred supplemental guess recaptured a different canvas');

  api.state.phase = 'ai_guess_feedback';
  api.state.chatInFlight = true;
  jpegMarker = 'auto-A';
  api.triggerRandomAiGuess();
  assertEqual(api.state.pendingAutoGuessImage, 'data:image/jpeg;base64,auto-A',
    'the automatic trigger did not queue its bounded snapshot');
  jpegMarker = 'auto-B';
  api.state.chatInFlight = false;
  api.flushDeferredAiGuessWork();
  await waitFor(() => events.commands.length === 2 && !api.state.aiGuessInFlight,
    'the deferred automatic guess did not finish');
  assertEqual(events.commands[1].payload.image_data_url, 'data:image/jpeg;base64,auto-A',
    'the deferred automatic guess recaptured a different canvas');
}

async function testBusyVisionRetryPreservesBoundedSnapshot() {
  const harness = loadHarness();
  const api = harness.api;
  const sourceCanvas = harness.sandbox.document.createElement('canvas');
  sourceCanvas.width = 800;
  sourceCanvas.height = 600;
  api.installCanvasForCapture(sourceCanvas);
  let jpegMarker = 'retry-A';
  harness.setCanvasDataUrlFactory(({ type }) => (
    type === 'image/png'
      ? 'data:image/png;base64,full-resolution'
      : `data:image/jpeg;base64,${jpegMarker}`
  ));
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  let attempt = 0;
  api.installRoundCommandSpies(() => {
    attempt += 1;
    return attempt === 1
      ? { ok: false, reason: 'session_busy' }
      : { ok: false, reason: 'test_response' };
  }, events);
  api.state.phase = 'ai_guess_feedback';
  api.state.roundFlowToken = 16;
  api.state.activeRoundToken = 16;

  api.triggerSupplementGuess(false);
  jpegMarker = 'retry-B';
  await waitFor(() => events.commands.length === 2 && !api.state.aiGuessInFlight,
    'the busy vision retry did not finish');

  assertEqual(events.commands[0].payload.image_data_url, 'data:image/jpeg;base64,retry-A',
    'the first vision request did not use the trigger snapshot');
  assertEqual(events.commands[1].payload.image_data_url, 'data:image/jpeg;base64,retry-A',
    'the busy retry recaptured a different canvas');
}

async function testBusyVisionRetryWaitsForInFlightChat() {
  const harness = loadHarness();
  const api = harness.api;
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  const sourceCanvas = harness.sandbox.document.createElement('canvas');
  sourceCanvas.width = 800;
  sourceCanvas.height = 600;
  api.installCanvasForCapture(sourceCanvas);
  const chat = deferred();
  let visionAttempt = 0;
  api.installRoundCommandSpies((command) => {
    if (command === 'round:input') return chat.promise;
    if (command !== 'round:vision-guess') throw new Error(`unexpected command: ${command}`);
    visionAttempt += 1;
    if (visionAttempt === 1) return { ok: false, reason: 'session_busy' };
    return {
      ok: true,
      message: 'vision recovered',
      guess: { label: 'train' },
      attempt: 1,
      max_attempts: 3,
      state: { phase: 'summary' },
    };
  }, events);
  api.state.phase = 'user_drawing';
  api.state.hasDrawn = true;
  api.state.roundFlowToken = 17;
  api.state.activeRoundToken = 17;

  const chatRequest = api.submitGameChat('still chatting');
  api.submitDrawing(true);
  await waitFor(() => visionAttempt === 1 && !api.state.aiGuessInFlight,
    'the initial vision request did not receive the simulated busy response');
  await new Promise((resolve) => setTimeout(resolve, 360));

  assertEqual(visionAttempt, 1,
    'the busy vision request retried while chat still owned the backend session lock');
  chat.resolve({ ok: true, message: 'chat finished' });
  await chatRequest;
  await waitFor(() => visionAttempt === 2 && events.summaries.length === 1 && !api.state.aiGuessInFlight,
    'the busy vision request did not retry after chat released the backend session lock');
  const visionCommands = events.commands.filter((call) => call.command === 'round:vision-guess');
  assertEqual(visionCommands.length, 2, 'the recovered vision request retried more than once');
  assertEqual(visionCommands[1].payload.image_data_url, visionCommands[0].payload.image_data_url,
    'the chat-delayed busy retry lost its original canvas snapshot');
  assertEqual(events.messages.length, 0, 'the recovered vision request surfaced an input failure');
  assertEqual(api.state.chatInFlight, false, 'the completed chat left its in-flight lock set');
}

async function testChatDelayedBusyVisionRetryStopsAfterRoundChange() {
  const harness = loadHarness();
  const api = harness.api;
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  api.installRoundCommandSpies((command) => {
    if (command !== 'round:vision-guess') throw new Error(`unexpected command: ${command}`);
    return { ok: false, reason: 'session_busy' };
  }, events);
  api.state.phase = 'ai_guessing';
  api.state.roundFlowToken = 18;
  api.state.activeRoundToken = 18;
  api.state.chatInFlight = true;

  await api.postVisionGuess('', { image_data_url: 'data:image/jpeg;base64,stale-retry' });
  api.state.roundFlowToken = 19;
  api.state.activeRoundToken = 19;
  api.state.chatInFlight = false;
  await new Promise((resolve) => setTimeout(resolve, 360));

  assertEqual(events.commands.length, 1,
    'a chat-delayed busy retry crossed into the next round');
}

async function testNewVisionGuessCancelsPendingBusyRetry() {
  const harness = loadHarness();
  const api = harness.api;
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  let attempt = 0;
  api.installRoundCommandSpies((command) => {
    if (command !== 'round:vision-guess') throw new Error(`unexpected command: ${command}`);
    attempt += 1;
    if (attempt === 1) return { ok: false, reason: 'session_busy' };
    return {
      ok: true,
      message: 'newer guess won',
      guess: { label: 'train' },
      attempt: 1,
      max_attempts: 3,
      state: { phase: 'ai_guess_feedback' },
    };
  }, events);
  api.state.phase = 'ai_guess_feedback';
  api.state.roundFlowToken = 20;
  api.state.activeRoundToken = 20;

  await api.postVisionGuess('', { image_data_url: 'data:image/jpeg;base64,old-retry' });
  assert(api.state.aiGuessBusyRetryTimer !== null,
    'the busy response did not schedule the retry under test');
  await api.postVisionGuess('', { image_data_url: 'data:image/jpeg;base64,new-guess' });
  await new Promise((resolve) => setTimeout(resolve, 360));

  assertEqual(events.commands.length, 2,
    'the superseded busy retry ran after a newer vision guess');
  assertEqual(events.commands[1].payload.image_data_url, 'data:image/jpeg;base64,new-guess',
    'the newer vision guess did not retain its own canvas snapshot');
  assertEqual(api.state.aiGuessBusyRetryTimer, null,
    'the newer vision guess left the superseded retry timer armed');
}

async function testStoppingAiGuessScheduleCancelsBusyRetry() {
  const harness = loadHarness();
  const api = harness.api;
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  api.installRoundCommandSpies((command) => {
    if (command !== 'round:vision-guess') throw new Error(`unexpected command: ${command}`);
    return { ok: false, reason: 'session_busy' };
  }, events);
  api.state.phase = 'ai_guessing';
  api.state.roundFlowToken = 21;
  api.state.activeRoundToken = 21;

  await api.postVisionGuess('', { image_data_url: 'data:image/jpeg;base64,cancelled-retry' });
  assert(api.state.aiGuessBusyRetryTimer !== null,
    'the busy response did not schedule the retry under test');
  api.stopAiGuessSchedule();
  await new Promise((resolve) => setTimeout(resolve, 360));

  assertEqual(events.commands.length, 1,
    'stopAiGuessSchedule did not cancel the pending busy retry');
  assertEqual(api.state.aiGuessBusyRetryTimer, null,
    'stopAiGuessSchedule left the busy retry timer armed');
}

async function testFeedbackGuessCancelsPendingBusyRetry() {
  const harness = loadHarness();
  const api = harness.api;
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  const sourceCanvas = harness.sandbox.document.createElement('canvas');
  sourceCanvas.width = 800;
  sourceCanvas.height = 600;
  api.installCanvasForCapture(sourceCanvas);
  let visionAttempts = 0;
  api.installRoundCommandSpies((command) => {
    if (command === 'round:vision-guess') {
      visionAttempts += 1;
      return { ok: false, reason: 'session_busy' };
    }
    if (command === 'round:feedback') {
      return {
        ok: true,
        kind: 'ai_guess',
        message: 'feedback guess won',
        guess: { label: 'train' },
        attempt: 1,
        max_attempts: 3,
        state: { phase: 'ai_guess_feedback' },
      };
    }
    throw new Error(`unexpected command: ${command}`);
  }, events);
  api.state.phase = 'ai_guess_feedback';
  api.state.roundFlowToken = 22;
  api.state.activeRoundToken = 22;

  await api.postVisionGuess('', { image_data_url: 'data:image/jpeg;base64,old-feedback-retry' });
  assert(api.state.aiGuessBusyRetryTimer !== null,
    'the busy response did not schedule the retry under test');
  await api.submitFeedbackInput('new hint', { request_id: 'new-feedback-guess' });
  await new Promise((resolve) => setTimeout(resolve, 360));

  assertEqual(visionAttempts, 1,
    'a busy vision retry ran after a newer feedback guess completed');
  assertEqual(api.state.aiGuessBusyRetryTimer, null,
    'the feedback guess left the superseded retry timer armed');
}

async function testRejectedVisionCommandUnlocksTheRound() {
  const harness = loadHarness();
  const api = harness.api;
  const sourceCanvas = harness.sandbox.document.createElement('canvas');
  sourceCanvas.width = 800;
  sourceCanvas.height = 600;
  api.installCanvasForCapture(sourceCanvas);
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  api.installRoundCommandSpies(() => {
    throw new Error('transport_failed');
  }, events);
  api.state.phase = 'ai_guess_feedback';
  api.state.roundFlowToken = 17;
  api.state.activeRoundToken = 17;

  await api.postVisionGuess('', {});

  assertEqual(events.commands.length, 1, 'the rejected vision command was not attempted');
  assertEqual(api.state.aiGuessInFlight, false,
    'a rejected vision command left the AI guess request locked');
  assertEqual(events.messages.length, 1,
    'a rejected fire-and-forget vision command did not surface a controlled failure');
}

async function testFailedJpegCaptureNeverSendsPngOrEmptyImage() {
  const harness = loadHarness();
  const api = harness.api;
  const sourceCanvas = harness.sandbox.document.createElement('canvas');
  sourceCanvas.width = 800;
  sourceCanvas.height = 600;
  api.installCanvasForCapture(sourceCanvas);
  const oversizedPng = `data:image/png;base64,${'x'.repeat(1800000)}`;
  harness.setCanvasDataUrlFactory(({ type }) => (type === 'image/png' ? oversizedPng : ''));
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  api.installRoundCommandSpies(() => ({ ok: true }), events);
  api.state.hasDrawn = true;
  api.state.roundFlowToken = 18;
  api.state.activeRoundToken = 18;

  api.state.phase = 'user_drawing';
  api.submitDrawing(true);
  api.state.phase = 'ai_guess_feedback';
  api.triggerSupplementGuess(false);
  api.triggerRandomAiGuess();
  await api.submitFeedbackInput('hint', { request_id: 'capture-failed' });
  await api.postVisionGuess('', { image_data_url: oversizedPng });

  assertEqual(events.commands.length, 0,
    'a failed JPEG capture sent a full PNG or empty image to a command');
  assertEqual(api.state.aiGuessInFlight, false,
    'a failed JPEG capture left the AI guess request locked');
  assertEqual(api.state.chatInFlight, false,
    'a failed feedback capture left chat input locked');
  assertEqual(api.state.userPng, oversizedPng,
    'a failed network capture discarded the local full-resolution PNG');
}

async function testAutomaticDrawingTimeoutSettlesWhenJpegCaptureFails() {
  const harness = loadHarness();
  const api = harness.api;
  const sourceCanvas = harness.sandbox.document.createElement('canvas');
  sourceCanvas.width = 800;
  sourceCanvas.height = 600;
  api.installCanvasForCapture(sourceCanvas);
  harness.setCanvasDataUrlFactory(({ type }) => (
    type === 'image/png' ? 'data:image/png;base64,full-resolution' : ''
  ));
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  const timeoutResponses = [
    { ok: true, phase: 'ai_guessing', state: { phase: 'ai_guessing' } },
    { ok: true, phase: 'summary', state: { phase: 'summary' }, evaluation: 'capture failed' },
  ];
  api.installRoundCommandSpies((command) => {
    if (command !== 'round:timeout') throw new Error(`unexpected command: ${command}`);
    return timeoutResponses.shift();
  }, events);
  api.state.phase = 'user_drawing';
  api.state.hasDrawn = true;
  api.state.roundFlowToken = 19;
  api.state.activeRoundToken = 19;

  api.submitDrawing(false);
  await waitFor(() => events.summaries.length === 1,
    'an automatic drawing timeout stayed stuck after JPEG capture failed');

  assertEqual(events.commands.length, 2,
    'capture failure did not follow the backend two-stage timeout contract');
  assert(events.commands.every((call) => call.command === 'round:timeout'),
    'capture failure sent a vision command without a valid JPEG');
  assertEqual(api.state.phase, 'summary',
    'automatic capture failure did not leave the user drawing phase');
}

async function testTimeoutResettlesAfterBackendPhaseAdvance() {
  const harness = loadHarness();
  const api = harness.api;
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  const responses = [
    { ok: true, phase: 'ai_guessing', state: { phase: 'ai_guessing' } },
    { ok: true, phase: 'summary', state: { phase: 'summary' }, evaluation: 'done' },
  ];
  api.installRoundCommandSpies(() => responses.shift(), events);
  api.state.phase = 'ai_guessing';
  api.state.roundFlowToken = 21;
  api.state.activeRoundToken = 21;

  await api.settleAiGuessTimeout();

  assertEqual(events.commands.length, 2,
    'timeout settlement did not continue after the backend advanced into AI guessing');
  events.commands.forEach((call) => {
    assertEqual(call.command, 'round:timeout', 'timeout recovery issued the wrong command');
    assertEqual(call.timeoutMs, 30000, 'timeout recovery lost its dedicated request budget');
  });
  assertEqual(events.summaries.length, 1, 'timeout recovery did not render exactly one summary');
}

async function testTimeoutServerBusyRetriesAreBounded() {
  const harness = loadHarness();
  const api = harness.api;
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  api.installRoundCommandSpies(() => ({ ok: false, reason: 'session_busy' }), events);
  api.setAiGuessTimeoutBusyMaxPolls(2);
  api.setAiGuessTimeoutBusyRetryTiming(1000, 5);
  api.state.phase = 'ai_guessing';
  api.state.roundFlowToken = 25;
  api.state.activeRoundToken = 25;

  await api.settleAiGuessTimeout();
  await waitFor(() => events.commands.length === 3 && events.messages.length === 1,
    'persistent server busy responses were not stopped at the retry limit');

  assertEqual(events.commands.length, 3, 'server busy settlement retried past its limit');
  assertEqual(events.messages[0].params.reason, 'session_busy',
    'server busy exhaustion reported the wrong failure');
}

async function testTimeoutServerBusyKeepsLockUntilRecoveredSummary() {
  const harness = loadHarness();
  const api = harness.api;
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.setAiGuessTimeoutBusyMaxPolls(20);
  api.setAiGuessTimeoutBusyRetryTiming(250, 5);
  api.installRoundCommandSpies(() => (
    events.commands.length <= 3
      ? { ok: false, reason: 'session_busy' }
      : {
          ok: true,
          phase: 'summary',
          state: { phase: 'summary', user_draw_answer: { id: 'cat', label: 'Cat' } },
          evaluation: 'Recovered.',
        }
  ), events);
  api.state.phase = 'ai_guess_feedback';
  api.state.routeActive = true;
  api.state.roundFlowToken = 27;
  api.state.activeRoundToken = 27;

  await api.settleAiGuessTimeout();
  assertEqual(api.state.aiGuessTimeoutSettling, true,
    'the first busy response released the timeout settlement lock');
  assertEqual(api.submitPlayerText('must stay locked'), false,
    'text input reopened while the backend transition was still busy');
  await waitFor(() => events.summaries.length === 1,
    'timeout settlement did not recover after the backend became ready');

  assertEqual(events.commands.length, 4,
    'timeout settlement did not retry the expected busy responses exactly once each');
  assertEqual(events.messages.length, 0,
    'recoverable busy responses surfaced a false round failure');
  assertEqual(api.state.aiGuessTimeoutSettling, false,
    'the recovered summary did not release the timeout settlement lock');
}

async function testTimeoutServerBusyUsesWallClockDeadline() {
  const harness = loadHarness();
  const api = harness.api;
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.setAiGuessTimeoutBusyMaxPolls(1000);
  api.setAiGuessTimeoutBusyRetryTiming(150, 10);
  api.installRoundCommandSpies(() => ({ ok: false, reason: 'session_busy' }), events);
  api.state.phase = 'ai_guessing';
  api.state.routeActive = true;
  api.state.roundFlowToken = 29;
  api.state.activeRoundToken = 29;

  await api.settleAiGuessTimeout();
  assertEqual(api.state.aiGuessTimeoutSettling, true,
    'the busy deadline released the lock before its retry window elapsed');
  await waitFor(() => events.messages.length === 1,
    'persistent server busy did not stop at the wall-clock deadline');

  assert(events.commands.length >= 2 && events.commands.length < 20,
    'the wall-clock deadline issued an unbounded number of busy retries');
  assertEqual(events.messages[0].params.reason, 'session_busy',
    'the wall-clock deadline reported the wrong failure');
  assertEqual(api.state.aiGuessTimeoutSettling, false,
    'the expired busy deadline did not release the input lock');
}

async function testTimeoutRecoversServerSummaryEvenWhenMarkedStale() {
  const harness = loadHarness();
  const api = harness.api;
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.installRoundCommandSpies(() => ({
    ok: false,
    reason: 'stale_timeout_phase',
    state: { phase: 'summary', user_draw_answer: { id: 'cat', label: 'Cat' } },
  }), events);
  api.state.phase = 'ai_guess_feedback';
  api.state.routeActive = true;
  api.state.roundFlowToken = 30;
  api.state.activeRoundToken = 30;

  await api.settleAiGuessTimeout();

  assertEqual(events.summaries.length, 1,
    'a server-confirmed summary was rejected because its timeout response was stale');
  assertEqual(events.messages.length, 0,
    'a recoverable server summary surfaced a false round failure');
  assertEqual(api.state.phase, 'summary',
    'a server-confirmed summary left the client in the guessing phase');
  assertEqual(api.state.aiGuessTimeoutSettling, false,
    'a recovered server summary did not release the input lock');
}

async function testDeferredTimeoutSettlesAfterVisionRequestFinishes() {
  const harness = loadHarness();
  const api = harness.api;
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.installRoundCommandSpies(() => ({
    ok: true,
    phase: 'summary',
    state: { phase: 'summary', user_draw_answer: { id: 'cat', label: 'Cat' } },
    evaluation: 'Settled after the request finished.',
  }), events);
  api.state.phase = 'ai_guess_feedback';
  api.state.routeActive = true;
  api.state.roundFlowToken = 32;
  api.state.activeRoundToken = 32;
  api.state.aiGuessInFlight = true;

  api.handleAiGuessTimeout();
  assertEqual(api.state.pendingAiGuessTimeout, true,
    'the countdown did not defer settlement behind an active vision request');
  assertEqual(api.state.aiGuessTimeoutSettling, true,
    'the deferred timeout did not lock new input immediately');
  assertEqual(events.commands.length, 0,
    'the deferred timeout raced the still-active vision request');

  api.state.aiGuessInFlight = false;
  api.flushDeferredAiGuessWork();
  await waitFor(() => events.summaries.length === 1,
    'the deferred timeout did not settle after the vision request finished');

  assertEqual(events.commands.length, 1,
    'the deferred timeout issued an unexpected number of settlement requests');
  assertEqual(events.commands[0].payload.timeout_kind, 'ai_guessing',
    'the deferred timeout lost its server-side phase fence');
  assertEqual(api.state.aiGuessTimeoutSettling, false,
    'the deferred timeout did not release its lock at summary');
}

async function testTimeoutPhaseAdvanceStopsWhenRoundChanges() {
  const harness = loadHarness();
  const api = harness.api;
  const events = { commands: [], messages: [], nekoMessages: [], phases: [], summaries: [] };
  api.installRoundCommandSpies(() => {
    api.state.roundFlowToken += 1;
    return { ok: true, phase: 'ai_guessing', state: { phase: 'ai_guessing' } };
  }, events);
  api.state.phase = 'ai_guessing';
  api.state.roundFlowToken = 31;
  api.state.activeRoundToken = 31;

  await api.settleAiGuessTimeout();

  assertEqual(events.commands.length, 1, 'a stale timeout response crossed into the next round');
  assertEqual(events.summaries.length, 0, 'a stale timeout response rendered a summary');
}

async function testUserGuessUsesFullClassifiedInputBudget() {
  const harness = loadHarness();
  const api = harness.api;
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.installRoundCommandSpies(() => ({ ok: true, correct: false, message: 'Try again.' }), events);
  api.state.phase = 'user_guessing';
  api.state.roundFlowToken = 41;
  api.state.activeRoundToken = 41;

  await api.submitUserGuess('maybe a train');

  assertEqual(events.commands.length, 1, 'a user guess issued an unexpected extra command');
  assertEqual(events.commands[0].command, 'round:input', 'a user guess used the wrong command');
  assertEqual(events.commands[0].timeoutMs, 30000,
    'a classified user guess did not receive the route input budget');

  await api.submitGameChat('chat while drawing');
  assertEqual(events.commands.length, 2, 'round chat issued an unexpected extra command');
  assertEqual(events.commands[1].command, 'round:input', 'round chat used the wrong command');
  assertEqual(events.commands[1].timeoutMs, 30000,
    'round chat did not receive the full route input budget');
}

function correctUserGuessResponse() {
  return {
    ok: true,
    correct: true,
    message: 'Correct.',
    answer: { id: 'train', label: 'Train' },
    user_draw_options: [{ id: 'cat', label: 'Cat' }],
    draw_seconds: 60,
  };
}

async function testGuessTimeoutRetryIsCancelledWhenPendingInputWins() {
  const harness = loadHarness();
  const api = harness.api;
  const inputResponse = deferred();
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.setGuessTimeoutRetryDelays(5, 5);
  api.installRoundCommandSpies((command) => {
    if (command === 'round:input') return inputResponse.promise;
    if (command === 'round:timeout') return { ok: false, reason: 'session_busy' };
    throw new Error(`unexpected command: ${command}`);
  }, events);
  api.state.phase = 'user_guessing';
  api.state.roundFlowToken = 43;
  api.state.activeRoundToken = 43;

  const inputPromise = api.submitUserGuess('train');
  api.state.phase = 'loading_round';
  await api.requestGuessTimeout(43, 0);
  assert(api.state.guessTimeoutRetryTimer !== null,
    'a busy guess timeout did not schedule its recovery retry');

  inputResponse.resolve(correctUserGuessResponse());
  await inputPromise;
  await new Promise((resolve) => setTimeout(resolve, 20));

  assertEqual(api.state.phase, 'drawing_pick', 'the winning input did not advance to word picking');
  assertEqual(api.state.guessTimeoutRetryTimer, null,
    'the winning input left a guess-timeout retry armed');
  assertEqual(events.commands.filter((call) => call.command === 'round:timeout').length, 1,
    'a stale guess-timeout retry ran after the answer advanced the round');
  assertEqual(events.commands.find((call) => call.command === 'round:timeout').payload.timeout_kind,
    'user_guessing', 'the guess timeout did not carry its server-side phase fence');
  assertEqual(events.userDrawPreparations.length, 1,
    'the winning input did not prepare exactly one drawing choice');
}

async function testLateGuessTimeoutFailureCannotRearmAfterInputWins() {
  const harness = loadHarness();
  const api = harness.api;
  const timeoutResponse = deferred();
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.setGuessTimeoutRetryDelays(5, 5);
  api.installRoundCommandSpies((command) => {
    if (command === 'round:timeout') return timeoutResponse.promise;
    if (command === 'round:input') return correctUserGuessResponse();
    throw new Error(`unexpected command: ${command}`);
  }, events);
  api.state.phase = 'loading_round';
  api.state.roundFlowToken = 47;
  api.state.activeRoundToken = 47;

  const timeoutPromise = api.requestGuessTimeout(47, 0);
  await api.submitUserGuess('train');
  timeoutResponse.reject(new Error('late timeout failure'));
  await timeoutPromise;
  await new Promise((resolve) => setTimeout(resolve, 20));

  assertEqual(api.state.phase, 'drawing_pick', 'a late timeout failure changed the advanced phase');
  assertEqual(api.state.guessTimeoutRetryTimer, null,
    'a late timeout failure re-armed recovery after the answer won');
  assertEqual(events.commands.filter((call) => call.command === 'round:timeout').length, 1,
    'a late timeout failure started a stale retry');
  assertEqual(events.messages.length, 0,
    'a stale timeout failure surfaced after the answer had already advanced');
}

async function testRecoveredGuessTimeoutWinsWithoutDoubleApplyingInput() {
  const harness = loadHarness();
  const api = harness.api;
  const inputResponse = deferred();
  const recoveredResponse = correctUserGuessResponse();
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.installRoundCommandSpies((command) => {
    if (command === 'round:input') return inputResponse.promise;
    if (command === 'round:timeout') return recoveredResponse;
    throw new Error(`unexpected command: ${command}`);
  }, events);
  api.state.phase = 'user_guessing';
  api.state.roundFlowToken = 49;
  api.state.activeRoundToken = 49;

  const inputPromise = api.submitUserGuess('train');
  api.state.phase = 'loading_round';
  await api.requestGuessTimeout(49, 0);

  assertEqual(api.state.phase, 'drawing_pick',
    'the recovered input transition did not advance to word picking');
  assertEqual(events.userDrawPreparations.length, 1,
    'the recovered input transition did not prepare exactly one drawing choice');

  inputResponse.resolve(recoveredResponse);
  await inputPromise;

  assertEqual(events.userDrawPreparations.length, 1,
    'the late original input response applied the recovered transition twice');
  assertEqual(events.nekoMessages.length, 1,
    'the late original input response repeated the recovered game message');
}

async function testGenericWordPickingTimeoutResponseRetriesWithoutBlankReveal() {
  const harness = loadHarness();
  const api = harness.api;
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.setGuessTimeoutRetryDelays(5, 5);
  api.installRoundCommandSpies(() => (
    events.commands.length === 1
      ? { ok: true, phase: 'word_picking', state: { phase: 'word_picking' } }
      : correctUserGuessResponse()
  ), events);
  api.state.phase = 'loading_round';
  api.state.roundFlowToken = 53;
  api.state.activeRoundToken = 53;

  await api.requestGuessTimeout(53, 0);

  assertEqual(events.nekoMessages.length, 0,
    'a phase-only timeout response rendered a blank answer reveal');
  assertEqual(events.userDrawPreparations.length, 0,
    'a phase-only timeout response prepared drawing choices without option data');
  assertEqual(api.state.phase, 'loading_round',
    'a phase-only timeout response displaced the pending input transition');
  await waitFor(() => events.userDrawPreparations.length === 1,
    'a phase-only timeout response was not retried to a recoverable transition');
  assertEqual(events.commands.length, 2,
    'a malformed timeout transition was retried an unexpected number of times');
}

async function testAiTimeoutSettlementLocksAllRoundInputAcrossPhaseAdvance() {
  const harness = loadHarness();
  const api = harness.api;
  const firstResponse = deferred();
  const secondResponse = deferred();
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.installRoundCommandSpies((_command, _payload, _timeoutMs) => (
    events.commands.length === 1 ? firstResponse.promise : secondResponse.promise
  ), events);
  api.state.phase = 'ai_guessing';
  api.state.routeActive = true;
  api.state.roundFlowToken = 59;
  api.state.activeRoundToken = 59;

  const settlementPromise = api.settleAiGuessTimeout();
  assertEqual(api.state.aiGuessTimeoutSettling, true,
    'the first timeout settlement request did not lock the round');
  assertEqual(api.submitPlayerText('one more hint'), false,
    'text input remained enabled while timeout settlement was in flight');
  api.triggerSupplementGuess(false, 'data:image/jpeg;base64,c3RhbGU=');
  assertEqual(events.commands.length, 1,
    'supplementary vision input escaped the timeout settlement lock');
  assertEqual(events.commands[0].payload.timeout_kind, 'ai_guessing',
    'AI timeout settlement did not carry its server-side phase fence');

  firstResponse.resolve({ ok: true, phase: 'ai_guessing', state: { phase: 'ai_guessing' } });
  await waitFor(() => events.commands.length === 2,
    'timeout settlement did not issue its required phase-advance request');
  assertEqual(api.state.aiGuessTimeoutSettling, true,
    'the settlement lock dropped between phase-advance requests');
  assertEqual(api.submitPlayerText('still locked'), false,
    'text input reopened between phase-advance requests');

  secondResponse.resolve({ ok: true, phase: 'summary', state: { phase: 'summary' }, evaluation: 'done' });
  await settlementPromise;

  assertEqual(events.summaries.length, 1, 'timeout settlement rendered the summary more than once');
  assertEqual(api.state.aiGuessTimeoutSettling, false,
    'successful timeout settlement did not release the input lock');
}

async function testAiTimeoutSettlementStaysLockedDuringNetworkBackoff() {
  const harness = loadHarness();
  const api = harness.api;
  const retryResponse = deferred();
  const events = {
    commands: [], messages: [], nekoMessages: [], phases: [], summaries: [], userDrawPreparations: [],
  };
  api.setAiGuessTimeoutRetryBaseDelay(5);
  api.installRoundCommandSpies(() => {
    if (events.commands.length === 1) return Promise.reject(new Error('temporary network failure'));
    return retryResponse.promise;
  }, events);
  api.state.phase = 'ai_guess_feedback';
  api.state.routeActive = true;
  api.state.roundFlowToken = 61;
  api.state.activeRoundToken = 61;

  await api.settleAiGuessTimeout();
  assertEqual(api.state.aiGuessTimeoutSettling, true,
    'a retryable network failure released the timeout settlement lock');
  assertEqual(api.submitPlayerText('retry gap'), false,
    'text input reopened during timeout retry backoff');
  await waitFor(() => events.commands.length === 2,
    'the timeout settlement network retry was not issued');
  assertEqual(api.state.aiGuessTimeoutSettling, true,
    'the timeout settlement retry did not retain the input lock');

  retryResponse.resolve({ ok: true, phase: 'summary', state: { phase: 'summary' }, evaluation: 'done' });
  await waitFor(() => events.summaries.length === 1,
    'the timeout settlement retry did not reach summary');
  assertEqual(api.state.aiGuessTimeoutSettling, false,
    'the retried timeout settlement did not release its input lock');
}

async function testRepeatedNekoRepliesAreRenderedAndSpoken() {
  const harness = loadHarness();
  const events = [];
  harness.api.installNekoMessageSpies(events);

  harness.api.addNekoMessage('Try again.');
  harness.api.addNekoMessage('Try again.');

  assertEqual(events.filter((event) => event.kind === 'bubble').length, 2,
    'a repeated valid fallback reply lost its chat bubble');
  assertEqual(events.filter((event) => event.kind === 'voice').length, 2,
    'a repeated valid fallback reply was not spoken');
  assertEqual(events.filter((event) => event.kind === 'mood').length, 2,
    'a repeated valid fallback reply did not animate the avatar');
}

async function runPrepareAiDrawingReview(responseFactory) {
  const harness = loadHarness();
  const api = harness.api;
  const calls = [];
  api.state.routeActive = true;
  api.state.routeEnding = false;
  api.state.phase = 'ai_drawing';
  api.state.roundFlowToken = 9;
  api.state.activeRoundToken = 9;
  api.state.sessionId = 'drawing-review-session';
  api.state.sdkClient = {
    disposed: false,
    runtime: { state: 'running', session: { id: 'drawing-review-session', routeInstanceId: 'review-route' } },
    commands: {
      execute(command, payload, options) {
        calls.push({ command, payload, options });
        return Promise.resolve().then(() => responseFactory(command, payload, options));
      },
    },
  };
  const original = sampleDrawingPlan();
  const prepared = await api.prepareAiDrawing({ plan: original, svg: '<svg>legacy</svg>' }, 9);
  return { harness, api, calls, original, prepared };
}

async function testDrawingPlanReviewUsesSdkAndAppliesOneReturnedPlan() {
  const corrected = sampleDrawingPlan('#f28c8c');
  const result = await runPrepareAiDrawingReview(() => ({
    ok: true,
    data: {
      ok: true,
      handled: true,
      accepted: false,
      corrected: true,
      drawing: { plan: corrected },
    },
  }));

  assertEqual(result.calls.length, 1, 'one drawing produced more than one visual review command');
  assertEqual(result.calls[0].command, 'round:ai-draw-review',
    'drawing review bypassed the declared SDK command');
  assertEqual(result.calls[0].payload.client_round_token, 9,
    'drawing review lost the active round token');
  assertEqual(result.calls[0].payload.image_data_url, 'data:image/jpeg;base64,384x288',
    'drawing review did not send the bounded local Canvas capture');
  assertDeepEqual(Object.keys(result.calls[0].payload).sort(),
    ['client_round_token', 'image_data_url'],
    'drawing review sent model plans or host-owned identity outside its SDK contract');
  assertEqual(result.calls[0].options.timeoutMs, 120000,
    'drawing review did not use its bounded command timeout');
  assertEqual(result.prepared.plan.elements[0].fill, '#f28c8c',
    'the single reviewed correction was not applied');
  assert(result.prepared.svg.includes('#f28c8c'),
    'the correction did not refresh the local summary/export SVG');
}

async function testDrawingPlanReviewUnavailableKeepsOriginalDrawing() {
  const result = await runPrepareAiDrawingReview(() => Promise.reject(
    Object.assign(new Error('vision unavailable'), { code: 'network_error' }),
  ));

  assertEqual(result.calls.length, 1, 'an unavailable reviewer was retried in a local loop');
  assertEqual(result.prepared.plan.elements[0].fill, '#f4cf45',
    'a review failure discarded the original local drawing plan');
  assert(result.prepared.svg.includes('#f4cf45'),
    'a review failure discarded the original summary/export artifact');
}

async function testStopSdkVoiceBestEffortRejectsResolvedFailureAndSyncThrow() {
  for (const voice of [
    { stop() { return Promise.resolve({ ok: false, active: false, reason: 'stop_failed' }); } },
    { stop() { throw new Error('stop_failed'); } },
  ]) {
    const harness = loadHarness();
    const api = harness.api;
    const client = {
      disposed: false,
      runtime: { state: 'running' },
      capabilities: { has(name) { return name === 'voice-input'; } },
      voice,
    };
    api.installVoiceUiSpy([]);
    api.state.sdkClient = client;
    api.state.voiceRouteActive = true;

    assertEqual(await api.stopSdkVoiceBestEffort(client), false,
      'a failed SDK voice stop was reported as successful');
  }
}

async function testSdkLoggerFailuresAreIsolated() {
  const harness = loadHarness();
  const result = harness.api.logSdkBestEffort({
    logger: {
      warn() { throw new Error('logger failed'); },
    },
  }, 'warn', 'runtime', 'route_inactive', 'safe message', { reason: 'inactive' });

  assertEqual(result, false, 'an SDK logger exception escaped the best-effort boundary');
}

async function testPageExitPostsVoiceStopBeforeCleanup() {
  const harness = loadHarness();
  const api = harness.api;
  const events = [];
  api.installPageExitCleanupSpy(events);
  api.state.sdkClient = {
    disposed: false,
    capabilities: { has(name) { return name === 'voice-input'; } },
    voice: {
      stop(options) {
        events.push(`stop:${options.timeoutMs}`);
        return Promise.resolve({ ok: true });
      },
    },
  };

  api.handleSdkPageExit();

  assertDeepEqual(events, ['stop:6500', 'cleanup'],
    'page exit must synchronously post the voice stop before local route cleanup');
}

async function main() {
  await testEndWaitsForRoundSessionCreation();
  await testWordChoiceRecoversCommittedBackendTransition();
  await testLateRoundStartCannotRestoreEndAfterCleanup();
  await testLateHydrationKeepsLocalSideAndColorChanges();
  await testLateModelViewHydrationMergesWithLocalPriority();
  await testCommittedWriteWaitsForHydrationBeforePersisting();
  await testFailedHydrationRetriesBeforeMergingAndWriting();
  await testPreferenceWritesAreSerializedAndCoalesceFinalSnapshot();
  await testFailedPreferenceWriteRetriesWithoutAnotherEdit();
  await testPreferenceWriteRetriesAreBoundedAndClientScoped();
  await testUnavailableStorageNeverFallsBackToRawLocalStorage();
  await testMemoryConsentUsesSdkAndRejectsLockedMismatch();
  await testPlayerTextCommandsStaySerialized();
  await testQueuedPlayerTextDoesNotCrossPhaseBoundary();
  await testVoiceStateCannotClearAnActiveControlRequest();
  await testBackgroundVoiceQueryCannotClearANewerToggle();
  await testVoiceToggleUsesOfficialSdkControl();
  await testRouteStartQueriesVoiceWithoutTakingOverMicrophone();
  await testBucketFillTreatsCanvasDisplayEdgeAsBoundary();
  await testCanvasDrawingPlanIsBoundedRenderedAndSerializable();
  await testRawAiSvgFillsResponsiveStage();
  await testComplexDrawingPlanSupportsCurvesAndMoreDetail();
  await testDrawingReviewCaptureIsLowResolutionOpaqueJpeg();
  await testVisionCommandsUseBoundedSnapshotsAndPreserveFullPng();
  await testDeferredVisionSnapshotsPreserveTriggerImage();
  await testBusyVisionRetryPreservesBoundedSnapshot();
  await testBusyVisionRetryWaitsForInFlightChat();
  await testChatDelayedBusyVisionRetryStopsAfterRoundChange();
  await testNewVisionGuessCancelsPendingBusyRetry();
  await testStoppingAiGuessScheduleCancelsBusyRetry();
  await testFeedbackGuessCancelsPendingBusyRetry();
  await testRejectedVisionCommandUnlocksTheRound();
  await testFailedJpegCaptureNeverSendsPngOrEmptyImage();
  await testAutomaticDrawingTimeoutSettlesWhenJpegCaptureFails();
  await testTimeoutResettlesAfterBackendPhaseAdvance();
  await testTimeoutServerBusyRetriesAreBounded();
  await testTimeoutServerBusyKeepsLockUntilRecoveredSummary();
  await testTimeoutServerBusyUsesWallClockDeadline();
  await testTimeoutRecoversServerSummaryEvenWhenMarkedStale();
  await testDeferredTimeoutSettlesAfterVisionRequestFinishes();
  await testTimeoutPhaseAdvanceStopsWhenRoundChanges();
  await testUserGuessUsesFullClassifiedInputBudget();
  await testGuessTimeoutRetryIsCancelledWhenPendingInputWins();
  await testLateGuessTimeoutFailureCannotRearmAfterInputWins();
  await testRecoveredGuessTimeoutWinsWithoutDoubleApplyingInput();
  await testGenericWordPickingTimeoutResponseRetriesWithoutBlankReveal();
  await testAiTimeoutSettlementLocksAllRoundInputAcrossPhaseAdvance();
  await testAiTimeoutSettlementStaysLockedDuringNetworkBackoff();
  await testRepeatedNekoRepliesAreRenderedAndSpoken();
  await testDrawingPlanReviewUsesSdkAndAppliesOneReturnedPlan();
  await testDrawingPlanReviewUnavailableKeepsOriginalDrawing();
  await testStopSdkVoiceBestEffortRejectsResolvedFailureAndSyncThrow();
  await testSdkLoggerFailuresAreIsolated();
  await testPageExitPostsVoiceStopBeforeCleanup();
  process.stdout.write('drawing guess SDK preference tests passed\n');
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
