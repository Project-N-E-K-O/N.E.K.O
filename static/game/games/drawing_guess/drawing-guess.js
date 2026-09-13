(function () {
  'use strict';

  var GAME_TYPE = 'drawing_guess';
  var SDK_GAME_ID = 'drawing-guess';
  var SDK_GAME_VERSION = '0.1.0';
  var ROUND_COMMANDS = Object.freeze({
    START: 'round:start',
    AI_DRAW: 'round:ai-draw',
    AI_DRAW_REVIEW: 'round:ai-draw-review',
    INPUT: 'round:input',
    FEEDBACK: 'round:feedback',
    CHOOSE_WORD: 'round:choose-word',
    TIMEOUT: 'round:timeout',
    VISION_GUESS: 'round:vision-guess'
  });
  var ROUND_COMMAND_RESPONSE_SCHEMA = Object.freeze({
    type: 'object',
    properties: { ok: { type: 'boolean' } },
    required: ['ok'],
    additionalProperties: true
  });
  var VISION_COMMAND_DATA_MAX_CHARS = 1800000;

  function roundCommandRequestSchema(extraProperties, requiredProperties) {
    return {
      request: {
        type: 'object',
        properties: Object.assign({
          client_round_token: { type: 'integer', minimum: 0 }
        }, extraProperties || {}),
        required: ['client_round_token'].concat(requiredProperties || []),
        additionalProperties: false
      },
      response: ROUND_COMMAND_RESPONSE_SCHEMA
    };
  }

  var ROUND_COMMAND_CONTRACTS = Object.freeze({
    'round:start': roundCommandRequestSchema(),
    'round:ai-draw': roundCommandRequestSchema(),
    'round:ai-draw-review': roundCommandRequestSchema({
      image_data_url: { type: 'string', maxLength: VISION_COMMAND_DATA_MAX_CHARS }
    }, ['image_data_url']),
    'round:input': roundCommandRequestSchema({
      text: { type: 'string', maxLength: 2000 },
      summary_chat_only: { type: 'boolean' },
      input_kind: { type: 'string', maxLength: 64 },
      source: { type: 'string', maxLength: 128 },
      request_id: { type: 'string', maxLength: 256 }
    }, ['text']),
    'round:feedback': roundCommandRequestSchema({
      text: { type: 'string', maxLength: 2000 },
      image_data_url: { type: 'string', maxLength: VISION_COMMAND_DATA_MAX_CHARS },
      input_kind: { type: 'string', maxLength: 64 },
      source: { type: 'string', maxLength: 128 },
      request_id: { type: 'string', maxLength: 256 }
    }, ['text', 'image_data_url']),
    'round:choose-word': roundCommandRequestSchema({
      word_id: { type: 'string', minLength: 1, maxLength: 64 }
    }, ['word_id']),
    'round:timeout': roundCommandRequestSchema({
      timeout_kind: { type: 'string', enum: ['user_guessing', 'ai_guessing'] }
    }, ['timeout_kind']),
    'round:vision-guess': roundCommandRequestSchema({
      image_data_url: { type: 'string', maxLength: VISION_COMMAND_DATA_MAX_CHARS },
      user_hint: { type: 'string', maxLength: 260 },
      settle_on_miss: { type: 'boolean' },
      time_expired: { type: 'boolean' }
    }, ['image_data_url'])
  });
  var ROUND_FALLBACK_SECONDS = 5 * 60;
  // Two bounded model-plan attempts can consume 64 seconds before the persona
  // line is generated. Match the host route budget so a valid late response is
  // not abandoned after the backend has already advanced the round.
  var AI_DRAW_REQUEST_TIMEOUT_MS = 90 * 1000;
  // Visual review may use 27 seconds and a rejected draft may then use two
  // 32-second plan-revision attempts. Leave bounded headroom above that
  // sequential 91-second model budget and match the registered host route.
  var AI_DRAW_REVIEW_REQUEST_TIMEOUT_MS = 120 * 1000;
  var AI_DRAW_PLAN_WIDTH = 800;
  var AI_DRAW_PLAN_HEIGHT = 600;
  var AI_DRAW_REVIEW_WIDTH = 384;
  var AI_DRAW_REVIEW_HEIGHT = 288;
  var AI_DRAW_PLAN_MAX_ELEMENTS = 240;
  var AI_DRAW_PLAN_MAX_POINTS_PER_ELEMENT = 256;
  var AI_DRAW_PLAN_MAX_TOTAL_POINTS = 4096;
  var AI_DRAW_PLAN_MAX_PATH_CHARS = 6000;
  var AI_DRAW_PLAN_MAX_PATH_COMMANDS = 512;
  var ROUND_INPUT_REQUEST_TIMEOUT_MS = 30 * 1000;
  // The backend may classify feedback and use the full vision-model budget
  // before producing the persona line, summary evaluation, and optional
  // memory write. Keep this aligned with the registered 350-second route.
  var AI_GUESS_REQUEST_TIMEOUT_MS = ROUND_FALLBACK_SECONDS * 1000 + 50000;
  var AI_GUESS_SETTLEMENT_REQUEST_TIMEOUT_MS = 30 * 1000;
  var AI_GUESS_TIMEOUT_MAX_RETRIES = 2;
  var AI_GUESS_TIMEOUT_PHASE_ADVANCE_MAX_RETRIES = 1;
  var AI_GUESS_TIMEOUT_BUSY_MAX_POLLS = 50;
  var AI_GUESS_TIMEOUT_BUSY_RETRY_WINDOW_MS = AI_GUESS_SETTLEMENT_REQUEST_TIMEOUT_MS;
  var AI_GUESS_TIMEOUT_BUSY_RETRY_DELAY_MS = 750;
  var AI_GUESS_TIMEOUT_RETRY_BASE_DELAY_MS = 500;
  var GUESS_TIMEOUT_RETRY_BASE_DELAY_MS = 1000;
  var GUESS_TIMEOUT_RETRY_MAX_DELAY_MS = 5000;
  var AI_GUESS_MIN_DELAY_MS = 10000;
  var AI_GUESS_MAX_DELAY_MS = 60000;
  var DRAW_PICK_DURATION_MS = 1450;
  var AI_DRAWING_PLACEHOLDER_DELAY_MS = 1200;
  var COLOR_HISTORY_VISIBLE_COUNT = 7;
  var COLOR_HISTORY_MAX_COUNT = 28;
  var MODEL_VIEW_SETTINGS_MAX_COUNT = 32;
  var SDK_PREFERENCE_HYDRATION_MAX_ATTEMPTS = 3;
  var SDK_PREFERENCE_HYDRATION_RETRY_DELAY_MS = 160;
  var SDK_PREFERENCE_WRITE_MAX_ATTEMPTS = 3;
  var SDK_PREFERENCE_WRITE_RETRY_DELAY_MS = 160;
  var SDK_ROUTE_CANVAS_DATA_MAX_CHARS = 200 * 1024;
  var boot = window.__DRAWING_GUESS_BOOT__ || {};

  var state = {
    sessionId: '',
    lanlanName: '',
    windowLanlanName: '',
    routeActive: false,
    routeEnding: false,
    sdkClient: null,
    sdkConnectPromise: null,
    sdkStartPromise: null,
    sdkReconcilePromise: null,
    sdkStateUnsubscribe: null,
    sdkInactiveUnsubscribe: null,
    sdkPageExitUnsubscribe: null,
    sdkSpeechStateUnsubscribe: null,
    sdkSpeechErrorUnsubscribe: null,
    sdkVoiceStateUnsubscribe: null,
    sdkVoiceTranscriptUnsubscribe: null,
    sdkVoiceErrorUnsubscribe: null,
    locale: 'zh-CN',
    sdkPulseForceRequestedSequence: 0,
    sdkPulseForceAcknowledgedSequence: 0,
    sdkPulsePayloadForce: false,
    sdkPulsePromise: null,
    sdkPulseGeneration: 0,
    countdownTimer: null,
    thinkingTimer: null,
    placeholderDotsTimer: null,
    aiDrawingPlaceholderTimer: null,
    sizePreviewTimer: null,
    colorPanelDrag: null,
    colorWheelPointerId: null,
    colorHistory: [],
    drawPickTimer: null,
    drawPickRevealTimer: null,
    aiGuessTimer: null,
    aiGuessBusyRetryTimer: null,
    aiGuessBusyRetryGeneration: 0,
    aiGuessNextAt: 0,
    nekoVoiceQueue: [],
    nekoVoiceInFlight: false,
    nekoVoiceController: null,
    lipSyncActive: false,
    lipSyncRetryTimer: null,
    lipSyncStopTimer: null,
    lipSyncRetryDeadline: 0,
    voiceRouteActive: false,
    voiceControlPending: false,
    voiceControlRequestSequence: 0,
    speechPlaybackActive: false,
    lastVoiceTranscriptRequestId: '',
    playerTextQueueGeneration: 0,
    playerTextChain: Promise.resolve(),
    canvasContextLastHash: '',
    canvasContextLastSentAt: 0,
    canvasContextLastClearAttemptAt: 0,
    canvasContextLastPayloadKind: '',
    thinkingMessageNode: null,
    modelMoodTimer: null,
    modelResizeHandler: null,
    roundFlowToken: 0,
    activeRoundToken: 0,
    roundSessionReady: false,
    roundRequestControllers: new Set(),
    guessTimeoutRetryTimer: null,
    aiGuessTimeoutRetryTimer: null,
    aiGuessDeadline: 0,
    aiGuessInFlight: false,
    chatInFlight: false,
    pendingAutoGuess: false,
    pendingAutoGuessImage: '',
    pendingSupplementGuess: false,
    pendingSupplementImage: '',
    pendingAiGuessTimeout: false,
    aiGuessTimeoutSettling: false,
    aiGuessAttempts: 0,
    maxAiGuessAttempts: 3,
    phase: 'tutorial',
    memoryConsent: 'none',
    aiSvg: '',
    aiDrawingPlan: null,
    aiDrawingAnimationToken: 0,
    aiAnswerLabel: '',
    userPng: '',
    userDrawAnswer: null,
    drawPickOptions: [],
    drawPickSeconds: ROUND_FALLBACK_SECONDS,
    drawPickChoosing: false,
    brushMode: 'brush',
    brushToolKind: 'brush',
    isDrawing: false,
    hasDrawn: false,
    history: [],
    redo: [],
    modelMood: 'idle',
    modelKind: 'fallback',
    modelLoadState: 'idle',
    modelView: {
      scale: 325.63,
      x: -0.96,
      y: 66.41
    },
    modelViewSettings: [],
    modelDrag: null,
    sideSplitRatio: 0.64,
    sideResize: null,
    avatarController: null,
    avatarMountPromise: null,
    avatarLoadToken: 0,
    roundNumber: 0,
    currentRoundSummarySaved: false,
    roundSummaries: []
  };

  var els = {};

  function $(id) {
    return document.getElementById(id);
  }

  function initEls() {
    els = {
      routeStatus: $('route-status'),
      characterName: $('character-name'),
      sessionId: $('session-id'),
      modelStage: $('model-stage'),
      sidePane: $('side-pane'),
      sideResizer: $('side-resizer'),
      live2dContainer: $('live2d-container'),
      live2dCanvas: $('live2d-canvas'),
      vrmContainer: $('vrm-container'),
      vrmCanvas: $('vrm-canvas'),
      mmdContainer: $('mmd-container'),
      mmdCanvas: $('mmd-canvas'),
      pngtuberContainer: $('pngtuber-container'),
      modelLoading: $('model-loading'),
      modelFallback: $('model-fallback-container'),
      modelResetControl: $('model-reset-control'),
      memoryState: $('memory-state'),
      doneButton: $('done-button'),
      nextRoundButton: $('next-round-button'),
      endButton: $('end-button'),
      clearCanvasButton: $('clear-canvas-button'),
      tutorialOverlay: $('tutorial-overlay'),
      tutorialStartButton: $('tutorial-start-button'),
      messageLog: $('message-log'),
      chatForm: $('chat-form'),
      chatInput: $('chat-input'),
      voiceRouteButton: $('voice-route-button'),
      voiceRouteIcon: $('voice-route-icon'),
      chatSubmit: document.querySelector('#chat-form button[type="submit"]'),
      canvasStage: $('canvas-stage'),
      placeholder: $('canvas-placeholder'),
      placeholderDetail: $('canvas-placeholder-detail'),
      drawPick: $('draw-pick'),
      aiDrawing: $('ai-drawing'),
      canvas: $('user-canvas'),
      summary: $('summary-view'),
      exitConfirm: $('exit-confirm'),
      exitStayButton: $('exit-stay-button'),
      exitLeaveButton: $('exit-leave-button'),
      exitReopenButton: $('exit-reopen-button'),
      badge: $('canvas-badge'),
      brushTool: $('brush-tool'),
      brushToolMenu: $('brush-tool-menu'),
      brushModeBrush: $('brush-mode-brush'),
      brushModeBucket: $('brush-mode-bucket'),
      eraserTool: $('eraser-tool'),
      undoTool: $('undo-tool'),
      redoTool: $('redo-tool'),
      brushColor: $('brush-color'),
      colorPanel: $('color-panel'),
      colorPanelHandle: $('color-panel-handle'),
      colorPanelClose: $('color-panel-close'),
      colorPanelToggle: $('color-panel-toggle'),
      eyedropperButton: $('eyedropper-button'),
      colorWheel: $('color-wheel'),
      colorTriggerPreview: $('color-trigger-preview'),
      colorPanelPreview: $('color-panel-preview'),
      colorHistoryColors: $('color-history-colors'),
      brushSize: $('brush-size'),
      eraserSize: $('eraser-size'),
      sizePreview: $('brush-size-preview'),
      sizePreviewRing: $('brush-size-preview-ring')
    };
    els.ctx = els.canvas.getContext('2d');
  }

  function t(key, fallback, params) {
    if (typeof window.t === 'function') {
      var translated = window.t(key, params || {});
      if (translated && translated !== key) return translated;
    }
    return String(fallback || '').replace(/\{\{(\w+)\}\}/g, function (_, name) {
      return params && params[name] != null ? String(params[name]) : '';
    });
  }

  function currentLanguage() {
    return String(state.locale || 'zh-CN');
  }

  var ZH_PLACEHOLDER_FALLBACKS = {
    'drawingGuess.input.guessPlaceholder': '输入你的猜测，或者要一个提示',
    'drawingGuess.input.hintPlaceholder': '先继续聊天；想让她再猜时再给提示',
    'drawingGuess.input.drawingPlaceholder': '画画时也可以聊天',
    'drawingGuess.input.summaryPlaceholder': '可以聊聊这一局，或者开始下一轮'
  };

  var ZH_TW_PLACEHOLDER_FALLBACKS = {
    'drawingGuess.input.guessPlaceholder': '輸入你的猜測，或者要一個提示',
    'drawingGuess.input.hintPlaceholder': '先繼續聊天；想讓她再猜時再給提示',
    'drawingGuess.input.drawingPlaceholder': '畫畫時也可以聊天',
    'drawingGuess.input.summaryPlaceholder': '可以聊聊這一局，或者開始下一輪'
  };

  function localizedFallback(key, fallback) {
    var language = currentLanguage().toLowerCase();
    if (language.indexOf('zh-tw') === 0 || language.indexOf('zh-hk') === 0 || language.indexOf('zh-hant') === 0) {
      return ZH_TW_PLACEHOLDER_FALLBACKS[key] || fallback;
    }
    if (language === 'zh' || language.indexOf('zh-cn') === 0 || language.indexOf('zh-hans') === 0) {
      return ZH_PLACEHOLDER_FALLBACKS[key] || fallback;
    }
    return fallback;
  }

  function makeSessionId() {
    return 'drawing-guess-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  }

  function makeRequestId(prefix) {
    return String(prefix || 'drawing-guess') + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  }

  function setStatus(key, fallback) {
    els.routeStatus.setAttribute('data-i18n', 'drawingGuess.status.' + key);
    els.routeStatus.textContent = t('drawingGuess.status.' + key, fallback);
  }

  var MODEL_STATE_FALLBACKS = {
    idle: 'Idle',
    drawing: 'Drawing',
    thinking: 'Thinking',
    guessing: 'Guessing',
    talking: 'Talking',
    happy: 'Happy',
    loading: 'Loading'
  };

  var MODEL_VIEW_DEFAULTS = {
    scale: 325.63,
    x: -0.96,
    y: 66.41
  };

  var SIDE_SPLIT_DEFAULT_RATIO = 0.64;
  var SIDE_MODEL_MIN_HEIGHT = 220;
  var SIDE_CHAT_MIN_HEIGHT = 280;
  var SIDE_RESIZER_HEIGHT = 12;

  function clampNumber(value, min, max, fallback) {
    var number = Number(value);
    if (!Number.isFinite(number)) number = fallback;
    return Math.max(min, Math.min(max, number));
  }

  function normalizeModelView(view) {
    view = view || {};
    return {
      scale: clampNumber(view.scale, 0.5, 5000, MODEL_VIEW_DEFAULTS.scale),
      x: clampNumber(view.x, -5000, 5000, MODEL_VIEW_DEFAULTS.x),
      y: clampNumber(view.y, -5000, 5000, MODEL_VIEW_DEFAULTS.y)
    };
  }

  function normalizeModelViewSettings(value) {
    var entries = Array.isArray(value) ? value : [];
    var seen = Object.create(null);
    var normalized = [];
    for (var i = 0; i < entries.length && normalized.length < MODEL_VIEW_SETTINGS_MAX_COUNT; i += 1) {
      var entry = entries[i];
      if (!entry || typeof entry !== 'object') continue;
      var character = String(entry.character || '').trim().slice(0, 128);
      if (!character || seen[character]) continue;
      var view = normalizeModelView(entry.view);
      // The old page used 100/0/0 as an implicit baseline. Do not revive that
      // stale value after the SDK migration changed the fitted default.
      if (view.scale === 100 && view.x === 0 && view.y === 0) continue;
      seen[character] = true;
      normalized.push({ character: character, view: view });
    }
    return normalized;
  }

  function modelViewSettingForCharacter(character) {
    var name = String(character || '').trim();
    for (var i = 0; i < state.modelViewSettings.length; i += 1) {
      if (state.modelViewSettings[i].character === name) return state.modelViewSettings[i].view;
    }
    return null;
  }

  var sdkPreferenceChannels = null;

  function createSdkPreferenceChannel(key, normalize, snapshot, apply) {
    return {
      key: key,
      normalize: normalize,
      snapshot: snapshot,
      apply: apply,
      revision: 0,
      loadEpoch: 0,
      hydrated: false,
      hydrating: false,
      hydrationPromise: null,
      hydrationFailures: 0,
      retryTimer: null,
      writeFailures: 0,
      writeRetryTimer: null,
      dirty: false,
      inFlight: false
    };
  }

  function ensureSdkPreferenceChannels() {
    if (sdkPreferenceChannels) return sdkPreferenceChannels;
    sdkPreferenceChannels = {
      modelViews: createSdkPreferenceChannel(
        'settings/model-views',
        normalizeModelViewSettings,
        function () { return normalizeModelViewSettings(state.modelViewSettings); },
        function (value, stale) {
          state.modelViewSettings = stale
            ? normalizeModelViewSettings(state.modelViewSettings.concat(value))
            : normalizeModelViewSettings(value);
          loadModelViewSettings();
        }
      ),
      sideSplit: createSdkPreferenceChannel(
        'settings/side-split-ratio',
        normalizeSideSplitRatio,
        function () { return normalizeSideSplitRatio(state.sideSplitRatio); },
        function (value, stale) {
          if (!stale) applySideSplitRatio(value, false);
        }
      ),
      colorHistory: createSdkPreferenceChannel(
        'settings/color-history',
        normalizeColorHistory,
        function () { return normalizeColorHistory(state.colorHistory); },
        function (value, stale) {
          if (stale) return;
          state.colorHistory = normalizeColorHistory(value);
          renderColorHistory();
        }
      )
    };
    return sdkPreferenceChannels;
  }

  function sdkStorageClient() {
    var client = state.sdkClient;
    return client && !client.disposed && client.capabilities.has('storage') ? client : null;
  }

  function flushSdkPreferenceChannel(channel) {
    // Do not persist a partial local snapshot before the first SDK read has
    // settled. In particular, model-view settings are shared by character: a
    // write racing the initial read could otherwise erase entries for every
    // character that has not been rendered in this page yet.
    if (!channel || !channel.hydrated || channel.inFlight || !channel.dirty) {
      return Promise.resolve(false);
    }
    var client = sdkStorageClient();
    if (!client) return Promise.resolve(false);
    var targetRevision = channel.revision;
    var snapshot = channel.snapshot();
    if (channel.writeRetryTimer) {
      clearTimeout(channel.writeRetryTimer);
      channel.writeRetryTimer = null;
    }
    channel.dirty = false;
    channel.inFlight = true;
    var writeFailed = false;
    var storageWrite;
    try {
      storageWrite = client.storage.set(channel.key, snapshot, { timeoutMs: 8000 });
    } catch (error) {
      storageWrite = Promise.reject(error);
    }
    return Promise.resolve(storageWrite).then(function (response) {
      var data = response && response.data && typeof response.data === 'object' ? response.data : {};
      if (!response || response.ok === false || data.ok === false || data.stored === false) {
        writeFailed = true;
        channel.dirty = true;
        return false;
      }
      channel.writeFailures = 0;
      return true;
    }).catch(function () {
      writeFailed = true;
      channel.dirty = true;
      return false;
    }).finally(function () {
      channel.inFlight = false;
      if (state.sdkClient !== client || client.disposed) return;
      if (channel.revision !== targetRevision) {
        channel.dirty = true;
        flushSdkPreferenceChannel(channel);
        return;
      }
      if (writeFailed && channel.dirty) {
        channel.writeFailures += 1;
        if (channel.writeFailures < SDK_PREFERENCE_WRITE_MAX_ATTEMPTS
            && !channel.writeRetryTimer) {
          channel.writeRetryTimer = setTimeout(function () {
            channel.writeRetryTimer = null;
            if (state.sdkClient !== client || client.disposed || !channel.hydrated
                || !channel.dirty || channel.revision !== targetRevision) return;
            flushSdkPreferenceChannel(channel);
          }, SDK_PREFERENCE_WRITE_RETRY_DELAY_MS * channel.writeFailures);
        }
      }
    });
  }

  function queueSdkPreferenceWrite(name) {
    var channel = ensureSdkPreferenceChannels()[name];
    if (!channel) return;
    if (channel.writeRetryTimer) {
      clearTimeout(channel.writeRetryTimer);
      channel.writeRetryTimer = null;
    }
    channel.writeFailures = 0;
    channel.revision += 1;
    channel.dirty = true;
    var client = sdkStorageClient();
    if (!channel.hydrated && client && !channel.hydrating && !channel.retryTimer
        && channel.hydrationFailures >= SDK_PREFERENCE_HYDRATION_MAX_ATTEMPTS) {
      channel.hydrationFailures = 0;
      hydrateSdkPreferenceChannel(client, channel);
    }
    // User gesture commit points start the SDK write in the same task. This
    // lets the SDK dispatch before a later pagehide disposes the client while
    // the in-flight/revision fence still coalesces rapid wheel/key events.
    flushSdkPreferenceChannel(channel);
  }

  function hydrateSdkPreferenceChannel(client, channel) {
    if (channel.hydrating && channel.hydrationPromise) return channel.hydrationPromise;
    if (channel.retryTimer) {
      clearTimeout(channel.retryTimer);
      channel.retryTimer = null;
    }
    var readRevision = channel.revision;
    var readEpoch = channel.loadEpoch + 1;
    channel.loadEpoch = readEpoch;
    channel.hydrating = true;
    var storageRead;
    try {
      storageRead = client.storage.get(channel.key, { timeoutMs: 8000 });
    } catch (error) {
      storageRead = Promise.reject(error);
    }
    var hydrationPromise = Promise.resolve(storageRead).then(function (response) {
      if (state.sdkClient !== client || client.disposed || channel.loadEpoch !== readEpoch) return false;
      var data = response && response.data && typeof response.data === 'object' ? response.data : {};
      if (!response || response.ok === false || data.ok === false || typeof data.found !== 'boolean') {
        throw new Error('sdk_preference_read_failed');
      }
      channel.hydrated = true;
      channel.hydrationFailures = 0;
      if (data.found === true) {
        channel.apply(channel.normalize(data.value), channel.dirty || channel.revision !== readRevision);
      }
      if (channel.dirty) flushSdkPreferenceChannel(channel);
      return true;
    }).catch(function () {
      if (state.sdkClient === client && !client.disposed && channel.loadEpoch === readEpoch) {
        // A failed read is not evidence that the key is absent. Keep dirty
        // local state fenced from writes until a later read can merge it with
        // any settings already stored for other characters.
        channel.hydrated = false;
        channel.hydrationFailures += 1;
        if (channel.hydrationFailures < SDK_PREFERENCE_HYDRATION_MAX_ATTEMPTS) {
          var failedEpoch = readEpoch;
          channel.retryTimer = setTimeout(function () {
            channel.retryTimer = null;
            if (state.sdkClient !== client || client.disposed || channel.hydrated
                || channel.loadEpoch !== failedEpoch) return;
            hydrateSdkPreferenceChannel(client, channel);
          }, SDK_PREFERENCE_HYDRATION_RETRY_DELAY_MS * channel.hydrationFailures);
        }
      }
      return false;
    }).finally(function () {
      if (channel.loadEpoch === readEpoch) {
        channel.hydrating = false;
        channel.hydrationPromise = null;
      }
    });
    channel.hydrationPromise = hydrationPromise;
    return hydrationPromise;
  }

  function hydrateSdkPreferences(client) {
    if (!client || !client.capabilities.has('storage')) return Promise.resolve(false);
    var channels = ensureSdkPreferenceChannels();
    return Promise.all(Object.keys(channels).map(function (name) {
      return hydrateSdkPreferenceChannel(client, channels[name]);
    })).then(function () { return true; });
  }

  function applyModelView() {
    var view = normalizeModelView(state.modelView);
    state.modelView = view;
    if (els.modelStage) {
      els.modelStage.style.setProperty('--dg-model-scale', String(view.scale / 100));
      els.modelStage.style.setProperty('--dg-model-offset-x', view.x + '%');
      els.modelStage.style.setProperty('--dg-model-offset-y', view.y + '%');
    }
    if (state.avatarController && !state.avatarController.disposed) {
      Promise.resolve(state.avatarController.setView(view)).catch(function () {});
    }
  }

  function setModelView(nextView, shouldSave) {
    state.modelView = normalizeModelView(Object.assign({}, state.modelView, nextView || {}));
    applyModelView();
    if (shouldSave) saveModelViewSettings();
  }

  function saveModelViewSettings() {
    var character = String(state.lanlanName || '').trim().slice(0, 128);
    if (!character) return;
    state.modelViewSettings = normalizeModelViewSettings([
      { character: character, view: normalizeModelView(state.modelView) }
    ].concat(state.modelViewSettings));
    queueSdkPreferenceWrite('modelViews');
  }

  function loadModelViewSettings() {
    var loaded = modelViewSettingForCharacter(state.lanlanName);
    state.modelView = normalizeModelView(loaded || MODEL_VIEW_DEFAULTS);
    applyModelView();
  }

  function resetModelView() {
    state.modelView = normalizeModelView(MODEL_VIEW_DEFAULTS);
    applyModelView();
    saveModelViewSettings();
  }

  function normalizeSideSplitRatio(value) {
    return clampNumber(value, 0.25, 0.82, SIDE_SPLIT_DEFAULT_RATIO);
  }

  function applySideSplitRatio(ratio, shouldSave) {
    if (!els.sidePane) return;
    var rect = els.sidePane.getBoundingClientRect();
    var divider = els.sideResizer ? Math.max(8, Math.round(els.sideResizer.getBoundingClientRect().height || SIDE_RESIZER_HEIGHT)) : SIDE_RESIZER_HEIGHT;
    var available = Math.max(1, Math.round((rect.height || 1) - divider));
    var minModelRatio = Math.min(1, SIDE_MODEL_MIN_HEIGHT / available);
    var maxModelRatio = Math.max(minModelRatio, (available - SIDE_CHAT_MIN_HEIGHT) / available);
    var normalized = clampNumber(ratio, minModelRatio, maxModelRatio, SIDE_SPLIT_DEFAULT_RATIO);
    state.sideSplitRatio = normalized;
    var minModelHeight = Math.min(SIDE_MODEL_MIN_HEIGHT, available);
    var maxModelHeight = Math.max(minModelHeight, available - SIDE_CHAT_MIN_HEIGHT);
    var modelHeight = Math.round(available * normalized);
    modelHeight = clampNumber(modelHeight, minModelHeight, maxModelHeight, minModelHeight);
    els.sidePane.style.gridTemplateRows = modelHeight + 'px ' + divider + 'px minmax(' + SIDE_CHAT_MIN_HEIGHT + 'px, 1fr)';
    if (els.sideResizer) {
      els.sideResizer.setAttribute('aria-valuemin', String(Math.round(minModelRatio * 100)));
      els.sideResizer.setAttribute('aria-valuemax', String(Math.round(maxModelRatio * 100)));
      els.sideResizer.setAttribute('aria-valuenow', String(Math.round(normalized * 100)));
    }
    if (!state.sideResize) {
      resizeActiveModelRenderer();
    }
    if (shouldSave) queueSdkPreferenceWrite('sideSplit');
  }

  function loadSideSplitRatio() {
    applySideSplitRatio(state.sideSplitRatio, false);
  }

  function beginSideResize(event) {
    if (!els.sidePane || !els.sideResizer || event.button !== 0) return;
    event.preventDefault();
    var rect = els.sidePane.getBoundingClientRect();
    var divider = Math.max(8, Math.round(els.sideResizer.getBoundingClientRect().height || 12));
    var available = Math.max(1, Math.round((rect.height || 1) - divider));
    state.sideResize = {
      pointerId: event.pointerId,
      top: rect.top,
      available: available
    };
    els.sidePane.classList.add('is-resizing');
    try { els.sideResizer.setPointerCapture(event.pointerId); } catch (_) {}
  }

  function moveSideResize(event) {
    if (!state.sideResize || state.sideResize.pointerId !== event.pointerId) return;
    event.preventDefault();
    var modelHeight = event.clientY - state.sideResize.top;
    applySideSplitRatio(modelHeight / state.sideResize.available, false);
  }

  function endSideResize(event) {
    if (!state.sideResize || state.sideResize.pointerId !== event.pointerId) return;
    event.preventDefault();
    state.sideResize = null;
    if (els.sidePane) els.sidePane.classList.remove('is-resizing');
    try { els.sideResizer.releasePointerCapture(event.pointerId); } catch (_) {}
    applySideSplitRatio(state.sideSplitRatio, true);
  }

  function handleSideResizeKey(event) {
    var delta = 0;
    if (event.key === 'ArrowUp') delta = -0.03;
    if (event.key === 'ArrowDown') delta = 0.03;
    if (!delta) return;
    event.preventDefault();
    applySideSplitRatio(state.sideSplitRatio + delta, true);
  }

  function handleModelWheel(event) {
    if (!els.modelStage) return;
    if (event.target && event.target.closest && event.target.closest('.dg-model-controls')) return;
    event.preventDefault();
    var current = normalizeModelView(state.modelView);
    var step = event.deltaY < 0 ? 1.08 : 1 / 1.08;
    if (event.ctrlKey || event.metaKey) step = event.deltaY < 0 ? 1.16 : 1 / 1.16;
    setModelView({ scale: current.scale * step }, true);
  }

  function modelViewTranslateTarget() {
    if (state.modelKind === 'vrm') return els.vrmContainer;
    if (state.modelKind === 'mmd') return els.mmdContainer;
    if (state.modelKind === 'pngtuber') return els.pngtuberContainer;
    if (state.modelKind === 'fallback' && els.modelFallback) {
      return els.modelFallback.querySelector('img') || els.modelFallback;
    }
    return null;
  }

  function modelViewDragReferenceSize() {
    var rect = els.modelStage ? els.modelStage.getBoundingClientRect() : null;
    var width = Math.max(1, rect && rect.width || 1);
    var height = Math.max(1, rect && rect.height || 1);
    var target = modelViewTranslateTarget();
    if (target) {
      width = Math.max(1, target.offsetWidth || target.clientWidth || width);
      height = Math.max(1, target.offsetHeight || target.clientHeight || height);
    }
    return { width: width, height: height };
  }

  function beginModelDrag(event) {
    if (!els.modelStage || event.button !== 0) return;
    if (event.target && event.target.closest && event.target.closest('.dg-model-controls')) return;
    event.preventDefault();
    state.modelDrag = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY
    };
    els.modelStage.classList.add('is-dragging');
    try { els.modelStage.setPointerCapture(event.pointerId); } catch (_) {}
  }

  function moveModelDrag(event) {
    if (!state.modelDrag || !els.modelStage || state.modelDrag.pointerId !== event.pointerId) return;
    event.preventDefault();
    var reference = modelViewDragReferenceSize();
    var dx = (event.clientX - state.modelDrag.x) / reference.width * 100;
    var dy = (event.clientY - state.modelDrag.y) / reference.height * 100;
    state.modelDrag.x = event.clientX;
    state.modelDrag.y = event.clientY;
    setModelView({
      x: state.modelView.x + dx,
      y: state.modelView.y + dy
    }, false);
  }

  function endModelDrag(event) {
    if (!state.modelDrag || state.modelDrag.pointerId !== event.pointerId) return;
    event.preventDefault();
    state.modelDrag = null;
    if (els.modelStage) {
      els.modelStage.classList.remove('is-dragging');
      try { els.modelStage.releasePointerCapture(event.pointerId); } catch (_) {}
    }
    saveModelViewSettings();
  }

  function modelMoodForPhase(phase) {
    if (phase === 'ai_drawing') return 'drawing';
    if (phase === 'loading_round' || phase === 'drawing_pick') return 'thinking';
    if (phase === 'ai_guessing' || phase === 'ai_guess_feedback') return 'guessing';
    if (phase === 'summary' || phase === 'final_summary') return 'happy';
    return 'idle';
  }

  function setModelKind(kind) {
    var normalized = String(kind || 'fallback').toLowerCase();
    state.modelKind = normalized;
    if (els.modelStage) els.modelStage.dataset.modelKind = normalized;
  }

  function setModelLoadState(loadState) {
    var normalized = String(loadState || 'idle').toLowerCase();
    state.modelLoadState = normalized;
    if (els.modelStage) els.modelStage.dataset.modelLoadState = normalized;
  }

  function showModelLayer(kind) {
    var normalized = String(kind || 'fallback').toLowerCase();
    [
      ['live2d', els.live2dContainer],
      ['vrm', els.vrmContainer],
      ['mmd', els.mmdContainer],
      ['pngtuber', els.pngtuberContainer],
      ['loading', els.modelLoading],
      ['fallback', els.modelFallback]
    ].forEach(function (pair) {
      var node = pair[1];
      if (!node) return;
      var shouldHide = pair[0] !== normalized;
      node.hidden = shouldHide;
      node.classList.toggle('hidden', shouldHide);
      node.style.display = shouldHide ? 'none' : '';
    });
    setModelKind(normalized);
    if (normalized === 'vrm' || normalized === 'mmd' || normalized === 'pngtuber') {
      applyEmbeddedModelSlotStyles(normalized);
    }
  }

  function setModelMood(mood, options) {
    var normalized = MODEL_STATE_FALLBACKS[mood] ? mood : 'idle';
    if (!options || !options.transient) {
      clearTimeout(state.modelMoodTimer);
      state.modelMoodTimer = null;
    }
    state.modelMood = normalized;
    if (els.modelStage) els.modelStage.dataset.modelMood = normalized;
    if (state.avatarController && !state.avatarController.disposed) {
      Promise.resolve(state.avatarController.setEmotion(normalized)).catch(function () {});
    }
  }

  function pulseModelMood(mood, durationMs) {
    if (!els.modelStage) return;
    clearTimeout(state.modelMoodTimer);
    setModelMood(mood, { transient: true });
    state.modelMoodTimer = setTimeout(function () {
      state.modelMoodTimer = null;
      setModelMood(modelMoodForPhase(state.phase));
    }, durationMs || 1600);
  }

  function applyEmbeddedModelSlotStyles(kind) {
    var container = null;
    var canvas = null;
    if (kind === 'vrm') {
      container = els.vrmContainer;
      canvas = els.vrmCanvas;
    } else if (kind === 'mmd') {
      container = els.mmdContainer;
      canvas = els.mmdCanvas;
    } else if (kind === 'pngtuber') {
      container = els.pngtuberContainer;
    }
    if (!container) return;
    container.hidden = false;
    container.classList.remove('hidden');
    container.style.display = '';
    container.style.position = 'absolute';
    container.style.inset = '0';
    container.style.top = '0';
    container.style.left = '0';
    container.style.right = '0';
    container.style.bottom = '0';
    container.style.width = '100%';
    container.style.height = '100%';
    container.style.visibility = 'visible';
    container.style.opacity = '1';
    container.style.setProperty('pointer-events', 'none', 'important');
    container.style.zIndex = '';
    if (canvas && canvas.style) {
      canvas.style.setProperty('pointer-events', 'none', 'important');
      canvas.style.maxWidth = '100%';
      canvas.style.maxHeight = '100%';
    }
  }

  function resizeActiveModelRenderer() {
    if (!state.avatarController || state.avatarController.disposed) return;
    Promise.resolve(state.avatarController.setView(normalizeModelView(state.modelView))).catch(function () {});
  }

  function setPhase(phase) {
    state.phase = phase;
    setModelMood(modelMoodForPhase(phase));
    updateControls();
  }

  function setBadge(text) {
    if (!text) {
      els.badge.classList.add('dg-hidden');
      els.badge.textContent = '';
      return;
    }
    els.badge.textContent = text;
    els.badge.classList.remove('dg-hidden');
  }

  function setChatPlaceholder(key, fallback) {
    els.chatInput.setAttribute('data-i18n-placeholder', key);
    els.chatInput.placeholder = t(key, localizedFallback(key, fallback));
  }

  function isCanvasEditablePhase() {
    return ['user_drawing', 'ai_guessing', 'ai_guess_feedback'].indexOf(state.phase) >= 0;
  }

  function isCanvasInteractionEnabled() {
    return !!state.lanlanName
      && state.routeActive
      && !state.routeEnding
      && !state.sdkStartPromise
      && !state.sdkReconcilePromise
      && !state.aiGuessTimeoutSettling
      && isCanvasEditablePhase();
  }

  function hasVoiceInputCapability() {
    return !!state.sdkClient
      && !state.sdkClient.disposed
      && state.sdkClient.capabilities.has('voice-input')
      && state.sdkClient.voice.connected;
  }

  function syncVoiceRouteButton() {
    if (!els.voiceRouteButton) return;
    var active = !!state.voiceRouteActive;
    var pending = !!state.voiceControlPending;
    var available = hasVoiceInputCapability();
    els.voiceRouteButton.disabled = !state.routeActive || state.routeEnding || pending || !available;
    els.voiceRouteButton.classList.toggle('is-active', active);
    els.voiceRouteButton.setAttribute('aria-pressed', active ? 'true' : 'false');
    els.voiceRouteButton.setAttribute('aria-busy', pending ? 'true' : 'false');
    if (!available) {
      els.voiceRouteButton.title = t('drawingGuess.voice.unavailable', 'In-game voice is unavailable in this environment.');
    } else if (pending) {
      els.voiceRouteButton.title = t('drawingGuess.voice.starting', 'Switching voice input…');
    } else if (active) {
      els.voiceRouteButton.title = t('drawingGuess.voice.connected', 'Voice is on for this round. Click to stop.');
    } else {
      els.voiceRouteButton.title = t('drawingGuess.voice.connectHint', 'Click to turn on voice for this round.');
    }
    if (els.voiceRouteIcon) {
      els.voiceRouteIcon.src = active ? '/static/icons/mic_icon_on.png' : '/static/icons/mic_icon_off.png';
    }
  }

  function updateControls() {
    var lifecycleBusy = state.routeEnding || !!state.sdkStartPromise || !!state.sdkReconcilePromise;
    var memoryLocked = lifecycleBusy || !!(state.sdkClient && state.sdkClient.memory.consent.locked);
    var routeReady = !!state.lanlanName && state.routeActive && !lifecycleBusy;
    var tutorialOpen = !!els.tutorialOverlay && !els.tutorialOverlay.hidden;
    var canvasEditable = isCanvasInteractionEnabled();
    var roundSummaryOpen = state.phase === 'summary';
    var finalSummaryOpen = state.phase === 'final_summary';
    els.characterName.textContent = state.lanlanName || '-';
    els.sessionId.textContent = state.sessionId || '-';
    els.memoryState.setAttribute('data-i18n', 'drawingGuess.memory.' + state.memoryConsent + 'Short');
    els.memoryState.textContent = t('drawingGuess.memory.' + state.memoryConsent + 'Short', state.memoryConsent);
    els.doneButton.hidden = roundSummaryOpen || finalSummaryOpen;
    els.nextRoundButton.hidden = !roundSummaryOpen;
    els.endButton.hidden = finalSummaryOpen;
    els.endButton.disabled = !routeReady || !state.roundSessionReady;
    els.doneButton.disabled = tutorialOpen || !routeReady || !canvasEditable;
    els.clearCanvasButton.disabled = !canvasEditable;
    els.nextRoundButton.disabled = state.phase !== 'summary' || !routeReady;
    els.chatSubmit.disabled = !routeReady || !playerTextPhaseAcceptsInput();
    els.chatInput.disabled = els.chatSubmit.disabled;
    els.undoTool.disabled = !canvasEditable || state.history.length <= 1;
    els.redoTool.disabled = !canvasEditable || state.redo.length === 0;
    document.querySelectorAll('input[name="memory-consent"]').forEach(function (input) {
      input.disabled = memoryLocked;
    });
    if (!canvasEditable || (state.brushMode === 'brush' && state.brushToolKind === 'bucket')) hideSizePreview();
    syncVoiceRouteButton();
  }

  function abortRoundRequests() {
    state.roundRequestControllers.forEach(function (controller) {
      try { controller.abort(); } catch (_) {}
    });
    state.roundRequestControllers.clear();
  }

  function beginRoundFlow() {
    abortRoundRequests();
    cancelGuessTimeoutRetry();
    clearTimeout(state.aiGuessTimeoutRetryTimer);
    state.aiGuessTimeoutRetryTimer = null;
    state.aiGuessTimeoutSettling = false;
    state.roundFlowToken += 1;
    state.playerTextQueueGeneration += 1;
    state.playerTextChain = Promise.resolve();
    return state.roundFlowToken;
  }

  function isCurrentRoundFlow(token) {
    return token === state.roundFlowToken;
  }

  function cancelGuessTimeoutRetry() {
    clearTimeout(state.guessTimeoutRetryTimer);
    state.guessTimeoutRetryTimer = null;
  }

  function staleRoundFlowError() {
    var err = new Error('stale_round_flow');
    err.staleRoundFlow = true;
    return err;
  }

  function ensureCurrentRoundFlow(token) {
    if (!isCurrentRoundFlow(token)) throw staleRoundFlowError();
  }

  function showExitConfirm() {
    if (!els.exitConfirm) return;
    hideExitReopenButton();
    els.exitConfirm.hidden = false;
    requestAnimationFrame(function () {
      els.exitConfirm.classList.add('is-open');
    });
  }

  function hideExitConfirm(shouldShowReopen) {
    if (!els.exitConfirm) return;
    els.exitConfirm.classList.remove('is-open');
    setTimeout(function () {
      if (!els.exitConfirm.classList.contains('is-open')) {
        els.exitConfirm.hidden = true;
        if (shouldShowReopen && state.phase === 'final_summary') {
          showExitReopenButton();
        }
      }
    }, 260);
  }

  function showExitReopenButton() {
    if (!els.exitReopenButton) return;
    els.exitReopenButton.hidden = false;
    requestAnimationFrame(function () {
      els.exitReopenButton.classList.add('is-visible');
    });
  }

  function hideExitReopenButton() {
    if (!els.exitReopenButton) return;
    els.exitReopenButton.classList.remove('is-visible');
    setTimeout(function () {
      if (!els.exitReopenButton.classList.contains('is-visible')) {
        els.exitReopenButton.hidden = true;
      }
    }, 190);
  }

  function deferExitConfirm() {
    hideExitConfirm(true);
  }

  function closeDrawingGuessBrowserFallback() {
    try { window.close(); } catch (_) {}
    setTimeout(function () {
      try {
        if (!window.closed) window.location.assign('/');
      } catch (_) {}
    }, 150);
  }

  function closeDrawingGuessWindow() {
    closeDrawingGuessBrowserFallback();
  }

  function leaveDrawingGuessPage() {
    hideExitReopenButton();
    hideExitConfirm(false);
    if (state.routeActive) {
      endRoute(false).finally(function () {
        closeDrawingGuessWindow();
      });
      return;
    }
    closeDrawingGuessWindow();
  }

  function startNextRound() {
    return startRound();
  }

  function addMessage(key, fallback, params, className) {
    var node = document.createElement('div');
    node.className = 'dg-message ' + (className || 'dg-message-system');
    node.textContent = key ? t(key, fallback, params || {}) : String(fallback || '');
    els.messageLog.appendChild(node);
    els.messageLog.scrollTop = els.messageLog.scrollHeight;
    return node;
  }

  function stopDrawingGuessLipSync() {
    clearTimeout(state.lipSyncRetryTimer);
    clearTimeout(state.lipSyncStopTimer);
    state.lipSyncRetryTimer = null;
    state.lipSyncStopTimer = null;
    state.lipSyncRetryDeadline = 0;
    if (state.avatarController && !state.avatarController.disposed) {
      Promise.resolve(state.avatarController.setSpeaking(false)).catch(function () {});
    }
    state.lipSyncActive = false;
  }

  function startDrawingGuessLipSync() {
    if (state.routeEnding || !state.routeActive) return false;
    if (!state.avatarController || state.avatarController.disposed) return false;
    if (state.lipSyncActive) return true;
    state.lipSyncActive = true;
    Promise.resolve(state.avatarController.setSpeaking(true)).then(function (started) {
      if (started === false) {
        state.lipSyncActive = false;
        if (Date.now() < state.lipSyncRetryDeadline && !state.lipSyncRetryTimer) {
          state.lipSyncRetryTimer = setTimeout(function () {
            state.lipSyncRetryTimer = null;
            startDrawingGuessLipSync();
          }, 120);
        }
      }
    }).catch(function () {
      state.lipSyncActive = false;
    });
    return true;
  }

  function scheduleDrawingGuessLipSyncStart() {
    clearTimeout(state.lipSyncRetryTimer);
    state.lipSyncRetryTimer = null;
    if (state.routeEnding || !state.routeActive) return;
    state.lipSyncRetryDeadline = Date.now() + 5000;
    if (startDrawingGuessLipSync()) return;
    function retry() {
      state.lipSyncRetryTimer = null;
      if (startDrawingGuessLipSync()) return;
      if (Date.now() < state.lipSyncRetryDeadline) {
        state.lipSyncRetryTimer = setTimeout(retry, 120);
      }
    }
    state.lipSyncRetryTimer = setTimeout(retry, 120);
  }

  function isSpeechPlaybackAudible(detail) {
    if (!detail || !detail.active) return false;
    var remaining = Number(detail.remainingSeconds || 0);
    if (remaining > 0.05) return true;
    var scheduledEnd = Number(detail.scheduledEndAudioTime || detail.playbackEndAudioTime || 0);
    var audioTime = Number(detail.audioContextTime || 0);
    return scheduledEnd > 0 && audioTime > 0 && scheduledEnd - audioTime > 0.05;
  }

  function speechPlaybackHasPendingAudioWork(detail) {
    return !!(detail && (
      detail.pendingAudioWork === true
      || detail.pending_audio_work === true
    ));
  }

  function armDrawingGuessLipSyncStop(detail) {
    clearTimeout(state.lipSyncStopTimer);
    state.lipSyncStopTimer = null;
    var remaining = Number(detail && detail.remainingSeconds || 0);
    if (!Number.isFinite(remaining) || remaining <= 0.05) return;
    var delay = Math.max(140, Math.min(30000, remaining * 1000 + 260));
    state.lipSyncStopTimer = setTimeout(function () {
      state.lipSyncStopTimer = null;
      stopDrawingGuessLipSync();
    }, delay);
  }

  function handleSpeechPlaybackState(playbackState) {
    var detail = (playbackState && playbackState.detail) || playbackState || {};
    if (isSpeechPlaybackAudible(detail)) {
      state.speechPlaybackActive = true;
      scheduleDrawingGuessLipSyncStart();
      armDrawingGuessLipSyncStop(detail);
      return;
    }
    state.speechPlaybackActive = !!detail.active || speechPlaybackHasPendingAudioWork(detail);
    stopDrawingGuessLipSync();
  }

  function clearNekoVoiceQueue() {
    state.nekoVoiceQueue = [];
    if (state.nekoVoiceController) {
      try { state.nekoVoiceController.abort(); } catch (_) {}
      state.nekoVoiceController = null;
    }
    state.nekoVoiceInFlight = false;
    stopDrawingGuessLipSync();
  }

  function flushNekoVoiceQueue() {
    if (state.nekoVoiceInFlight || !state.nekoVoiceQueue.length) return;
    if (!state.routeActive || state.routeEnding || !state.lanlanName) {
      state.nekoVoiceQueue = [];
      return;
    }
    var item = state.nekoVoiceQueue.shift();
    if (!item || !item.line) {
      flushNekoVoiceQueue();
      return;
    }
    if (item.roundFlowToken !== state.roundFlowToken
      || item.sessionId !== state.sessionId
      || item.routeInstanceId !== sdkRouteInstanceId()) {
      flushNekoVoiceQueue();
      return;
    }
    var client = state.sdkClient;
    if (!client || !client.capabilities.has('speech-output')) {
      state.nekoVoiceQueue = [];
      return;
    }
    var controller = new AbortController();
    var speechFlowToken = item.roundFlowToken;
    var speechSessionId = item.sessionId;
    var speechRouteInstanceId = item.routeInstanceId;
    state.nekoVoiceController = controller;
    state.nekoVoiceInFlight = true;
    client.speech.speak({
      text: item.line,
      source: 'game-llm-result',
      requestId: item.requestId,
      eventKey: 'drawing-guess:' + String(state.roundNumber),
      mirrorText: false,
      emitTurnEnd: true,
      interruptExisting: false,
      event: {
        kind: 'drawing_guess_neko_line',
        source: 'drawing_guess',
        phase: state.phase,
        round: state.roundNumber,
        text_length: item.line.length
      }
    }, { signal: controller.signal, timeoutMs: 70000 }).then(function (response) {
      var data = sdkResponseData(response);
      if (!response || response.ok === false || data.ok === false) {
        var error = new Error(String((data && data.reason) || 'speech_output_failed'));
        error.code = String((data && data.reason) || 'speech_output_failed');
        throw error;
      }
    }).catch(function (error) {
      if (controller.signal.aborted
        || !isCurrentRoundFlow(speechFlowToken)
        || speechSessionId !== state.sessionId
        || speechRouteInstanceId !== sdkRouteInstanceId()) return;
      if (state.sdkClient) {
        logSdkBestEffort(state.sdkClient, 'warn', 'speech', 'speak_failed', '小游戏 SDK 语音输出失败', {
          reason: sdkErrorReason(error)
        });
      }
    }).finally(function () {
      if (state.nekoVoiceController !== controller) return;
      state.nekoVoiceController = null;
      state.nekoVoiceInFlight = false;
      if (isCurrentRoundFlow(speechFlowToken)
        && speechSessionId === state.sessionId
        && speechRouteInstanceId === sdkRouteInstanceId()
        && state.nekoVoiceQueue.length) {
        setTimeout(flushNekoVoiceQueue, 120);
      }
    });
  }

  function enqueueNekoVoice(text) {
    var line = String(text || '').replace(/\s+/g, ' ').trim();
    if (!line || !state.lanlanName || state.routeEnding) return;
    state.nekoVoiceQueue.push({
      line: line,
      requestId: makeRequestId('drawing-guess-voice'),
      roundFlowToken: state.roundFlowToken,
      sessionId: state.sessionId,
      routeInstanceId: sdkRouteInstanceId()
    });
    if (state.nekoVoiceQueue.length > 5) {
      state.nekoVoiceQueue.splice(0, state.nekoVoiceQueue.length - 5);
    }
    flushNekoVoiceQueue();
  }

  function addNekoMessage(text) {
    var normalized = String(text || '').replace(/\s+/g, ' ').trim();
    if (!normalized) return;
    addMessage('', text, null, 'dg-message-neko');
    enqueueNekoVoice(normalized);
    pulseModelMood('talking', Math.min(2800, Math.max(1200, String(text).length * 45)));
  }

  function addUserMessage(text) {
    if (text) addMessage('', text, null, 'dg-message-user');
  }

  function addEventMessage(key, fallback, params) {
    return addMessage(key, fallback, params || {}, 'dg-message-event');
  }

  function stopThinkingEventMessage() {
    clearInterval(state.thinkingTimer);
    state.thinkingTimer = null;
    state.thinkingMessageNode = null;
  }

  function dotAnimationBase(text) {
    return String(text || '').replace(/[.\u3002\u2026\uff0e]+$/g, '');
  }

  function startDotAnimation(node, text, afterRender) {
    var base = dotAnimationBase(text);
    var step = 1;
    function render() {
      if (!node) return;
      node.textContent = base + '.'.repeat(step);
      if (typeof afterRender === 'function') afterRender();
    }
    render();
    return setInterval(function () {
      step = step % 3 + 1;
      render();
    }, 500);
  }

  function clearAiDrawingPlaceholderHint(restoreDefault) {
    clearTimeout(state.aiDrawingPlaceholderTimer);
    state.aiDrawingPlaceholderTimer = null;
    clearInterval(state.placeholderDotsTimer);
    state.placeholderDotsTimer = null;
    if (restoreDefault) {
      setCanvasPlaceholderDetail('drawingGuess.layout.canvasWaiting', 'After starting, she draws first and you guess. Then you get 5 minutes to draw something for her.');
    }
  }

  function setCanvasPlaceholderDetail(key, fallback) {
    if (!els.placeholderDetail) return;
    els.placeholderDetail.setAttribute('data-i18n', key);
    els.placeholderDetail.textContent = t(key, fallback);
  }

  function startCanvasPlaceholderDots(key, fallback) {
    if (!els.placeholderDetail) return;
    clearInterval(state.placeholderDotsTimer);
    state.placeholderDotsTimer = null;
    els.placeholderDetail.setAttribute('data-i18n', key);
    state.placeholderDotsTimer = startDotAnimation(els.placeholderDetail, t(key, fallback));
  }

  function scheduleAiDrawingPlaceholderHint() {
    clearAiDrawingPlaceholderHint(false);
    state.aiDrawingPlaceholderTimer = setTimeout(function () {
      state.aiDrawingPlaceholderTimer = null;
      if (state.phase !== 'ai_drawing') return;
      if (!els.placeholder || els.placeholder.classList.contains('dg-hidden')) return;
      startCanvasPlaceholderDots('drawingGuess.messages.aiDrawingWaiting', 'She is drawing');
    }, AI_DRAWING_PLACEHOLDER_DELAY_MS);
  }

  function stopDrawPickAnimation() {
    clearTimeout(state.drawPickTimer);
    clearTimeout(state.drawPickRevealTimer);
    state.drawPickTimer = null;
    state.drawPickRevealTimer = null;
    state.drawPickChoosing = false;
  }

  function cancelAiGuessBusyRetry() {
    clearTimeout(state.aiGuessBusyRetryTimer);
    state.aiGuessBusyRetryTimer = null;
    state.aiGuessBusyRetryGeneration += 1;
  }

  function stopAiGuessSchedule() {
    clearTimeout(state.aiGuessTimer);
    state.aiGuessTimer = null;
    cancelAiGuessBusyRetry();
    clearTimeout(state.aiGuessTimeoutRetryTimer);
    state.aiGuessTimeoutRetryTimer = null;
    state.aiGuessNextAt = 0;
    state.pendingAutoGuess = false;
    state.pendingAutoGuessImage = '';
    state.pendingSupplementGuess = false;
    state.pendingSupplementImage = '';
    state.pendingAiGuessTimeout = false;
    state.aiGuessDeadline = 0;
  }

  function startThinkingEventMessage(key, fallback) {
    stopThinkingEventMessage();
    var node = addEventMessage('', '');
    state.thinkingMessageNode = node;
    state.thinkingTimer = startDotAnimation(node, t(key, fallback), function () {
      els.messageLog.scrollTop = els.messageLog.scrollHeight;
    });
    return node;
  }

  function executeRoundCommand(command, payload, timeoutMs) {
    // The SDK owns endpoint selection, trusted route identity, response schema
    // validation and transport cancellation. The round token remains a second,
    // game-level fence because a new round can start inside one live SDK route.
    if (!state.routeActive || state.routeEnding) return Promise.resolve(undefined);
    var controller = new AbortController();
    var requestFlowToken = state.roundFlowToken;
    state.roundRequestControllers.add(controller);
    return connectMiniGameSdk().then(function (client) {
      return client.commands.execute(command, payload || {}, {
        signal: controller.signal,
        timeoutMs: timeoutMs || 10000
      });
    }).then(function (response) {
      return sdkResponseData(response);
    }).then(function (data) {
      // Route termination, SDK inactivity, or a new round invalidates every
      // earlier command. Do not let its state projection land before the
      // caller's own stale-flow guard gets a chance to reject it.
      return data;
    }).catch(function (err) {
      if (!isCurrentRoundFlow(requestFlowToken)) {
        // Lifecycle cleanup aborts every request from the retired round. Treat
        // that as a neutral stale result: callers already guard their `.then`
        // handlers with the captured round token, while rejecting here would
        // create unhandled promises for fire-and-forget chat/guess actions.
        return undefined;
      }
      if (err && (err.code === 'timeout' || err.code === 'request_timeout')) {
        var timeoutError = new Error('request_timeout');
        timeoutError.code = 'request_timeout';
        throw timeoutError;
      }
      throw err;
    }).finally(function () {
      state.roundRequestControllers.delete(controller);
    });
  }

  function readableRequestError(err) {
    if (err && (err.code === 'request_timeout' || err.code === 'timeout' || err.message === 'request_timeout')) {
      return t('drawingGuess.messages.requestTimeout', 'Request timed out. Please try again.');
    }
    return err && err.message ? err.message : 'unknown';
  }

  function shouldShareCanvasContext() {
    return state.routeActive && state.hasDrawn && ['user_drawing', 'ai_guessing', 'ai_guess_feedback'].indexOf(state.phase) >= 0;
  }

  function canvasDataHash(dataUrl) {
    if (!dataUrl) return '';
    return [
      String(dataUrl.length),
      dataUrl.slice(0, 48),
      dataUrl.slice(Math.max(0, dataUrl.length - 48))
    ].join(':');
  }

  function captureRouteCanvasSnapshot() {
    if (!els.canvas) return '';
    try {
      var sourceWidth = Math.max(1, Number(els.canvas.width) || 800);
      var sourceHeight = Math.max(1, Number(els.canvas.height) || 600);
      var scale = Math.min(1, 480 / sourceWidth, 360 / sourceHeight);
      var canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(sourceWidth * scale));
      canvas.height = Math.max(1, Math.round(sourceHeight * scale));
      var context = canvas.getContext('2d');
      context.fillStyle = '#fffdfa';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(els.canvas, 0, 0, canvas.width, canvas.height);
      var qualities = [0.72, 0.58, 0.44, 0.32];
      for (var i = 0; i < qualities.length; i += 1) {
        var dataUrl = canvas.toDataURL('image/jpeg', qualities[i]);
        if (dataUrl && dataUrl.length <= SDK_ROUTE_CANVAS_DATA_MAX_CHARS) return dataUrl;
      }
    } catch (_) {}
    return '';
  }

  function boundedVisionCommandImage(value) {
    var dataUrl = String(value || '');
    if (dataUrl.indexOf('data:image/jpeg;base64,') !== 0) return '';
    if (dataUrl.length > VISION_COMMAND_DATA_MAX_CHARS) return '';
    return dataUrl;
  }

  function captureVisionCommandImage() {
    return boundedVisionCommandImage(captureRouteCanvasSnapshot());
  }

  function canvasContextPayload(force) {
    state.canvasContextLastPayloadKind = '';
    if (!shouldShareCanvasContext()) {
      if (state.canvasContextLastHash) {
        var clearNow = Date.now();
        if (!force && clearNow - state.canvasContextLastClearAttemptAt < 15000) return {};
        state.canvasContextLastClearAttemptAt = clearNow;
        state.canvasContextLastPayloadKind = 'clear';
        return { canvas_context_clear: true };
      }
      return {};
    }
    var dataUrl = captureRouteCanvasSnapshot();
    if (!dataUrl || dataUrl.length > SDK_ROUTE_CANVAS_DATA_MAX_CHARS) {
      // 当前画布导出失败/超限时不能留着服务端旧快照，否则后续的
      // 视觉猜测会拿到过期画面
      if (state.canvasContextLastHash) {
        var failedCaptureNow = Date.now();
        if (!force && failedCaptureNow - state.canvasContextLastClearAttemptAt < 15000) return {};
        state.canvasContextLastClearAttemptAt = failedCaptureNow;
        state.canvasContextLastPayloadKind = 'clear';
        return { canvas_context_clear: true };
      }
      return {};
    }
    var now = Date.now();
    var hash = canvasDataHash(dataUrl);
    if (!force && hash === state.canvasContextLastHash && now - state.canvasContextLastSentAt < 15000) {
      return {};
    }
    state.canvasContextLastHash = hash;
    state.canvasContextLastSentAt = now;
    state.canvasContextLastClearAttemptAt = 0;
    state.canvasContextLastPayloadKind = 'image';
    return { canvas_image_data_url: dataUrl };
  }

  function sdkRouteInstanceId() {
    var client = state.sdkClient;
    return String((client && client.runtime && client.runtime.session
      && client.runtime.session.routeInstanceId) || '').trim();
  }

  function routePayload(extra) {
    return Object.assign({
      lanlan_name: state.lanlanName,
      window_lanlan_name: state.windowLanlanName || state.lanlanName,
      source: 'drawing_guess',
      gameStarted: state.phase !== 'tutorial',
      game_started: state.phase !== 'tutorial',
      client_round_token: state.roundFlowToken,
      currentState: {
        game: GAME_TYPE,
        phase: state.phase,
        client_round_token: state.roundFlowToken,
        round: state.roundNumber,
        has_user_canvas: !!state.hasDrawn,
        canvas_context_visible: shouldShareCanvasContext()
      }
    }, extra || {});
  }

  function sdkRuntimePayload() {
    var visible = !document.hidden;
    var forceCanvas = state.sdkPulsePayloadForce;
    var canvasPayload = canvasContextPayload(forceCanvas);
    return routePayload(Object.assign({
      visible: visible,
      pageVisible: visible,
      visibilityState: document.visibilityState || (visible ? 'visible' : 'hidden')
    }, canvasPayload));
  }

  function sdkRouteEndPayload(options) {
    options = options || {};
    var completedRoute = options.completedRoute === undefined
      ? (!!options.finalSummary || state.phase === 'summary' || state.phase === 'final_summary')
      : !!options.completedRoute;
    return routePayload({
      reason: options.reason || (completedRoute ? 'drawing_guess_game_over' : 'drawing_guess_abandoned'),
      roundCompleted: completedRoute,
      round_completed: completedRoute,
      postgameProactive: false,
      suppressWindowStateChange: options.suppressWindowStateChange === true,
      suppressRouteEndStatus: options.suppressRouteEndStatus === true
    });
  }

  function sdkResponseData(response) {
    return response && response.data && typeof response.data === 'object' ? response.data : {};
  }

  function sdkErrorReason(error) {
    return String((error && (error.code || error.message)) || 'request_failed');
  }

  function logSdkBestEffort(client, level, category, event, message, details) {
    try {
      var logger = client && client.logger;
      if (!logger || typeof logger[level] !== 'function') return false;
      logger[level](category, event, message, details);
      return true;
    } catch (_) {
      return false;
    }
  }

  function isSdkRouteRunning(client) {
    return !!client
      && state.sdkClient === client
      && state.routeActive
      && !state.routeEnding
      && client.runtime.state === 'running';
  }

  function cleanupRouteResources() {
    beginRoundFlow();
    state.activeRoundToken = state.roundFlowToken;
    state.roundSessionReady = false;
    state.isDrawing = false;
    if (els.ctx) {
      els.ctx.globalCompositeOperation = 'source-over';
      els.ctx.beginPath();
    }
    hideSizePreview();
    state.aiGuessInFlight = false;
    state.chatInFlight = false;
    state.pendingAutoGuess = false;
    state.pendingAutoGuessImage = '';
    state.pendingSupplementGuess = false;
    state.pendingSupplementImage = '';
    state.pendingAiGuessTimeout = false;
    stopThinkingEventMessage();
    clearNekoVoiceQueue();
    state.voiceRouteActive = false;
    state.voiceControlPending = false;
    state.voiceControlRequestSequence += 1;
    state.lastVoiceTranscriptRequestId = '';
    state.canvasContextLastHash = '';
    state.canvasContextLastSentAt = 0;
    state.canvasContextLastClearAttemptAt = 0;
    state.canvasContextLastPayloadKind = '';
    state.sdkPulseForceRequestedSequence = 0;
    state.sdkPulseForceAcknowledgedSequence = 0;
    state.sdkPulsePayloadForce = false;
    state.sdkPulsePromise = null;
    state.sdkPulseGeneration += 1;
    stopCountdown();
    stopDrawPickAnimation();
    stopAiGuessSchedule();
  }

  function handleSdkRuntimeState(event) {
    var current = String((event && event.payload && event.payload.current) || '');
    if (current === 'running') {
      // The SDK starts route monitoring before runtime.start() resolves to this
      // page. Mirror its lifecycle event synchronously so local controls cannot
      // remain fenced behind the still-pending start Promise.
      state.routeActive = true;
    } else if (['idle', 'ended', 'inactive'].indexOf(current) >= 0) {
      state.routeActive = false;
    }
    updateControls();
  }

  function handleSdkRuntimeInactive(event) {
    state.routeActive = false;
    state.routeEnding = false;
    cleanupRouteResources();
    showPlaceholder();
    if (els.tutorialOverlay) els.tutorialOverlay.hidden = false;
    setPhase('tutorial');
    setStatus('heartbeatLost', 'Route inactive');
    if (state.sdkClient) {
      logSdkBestEffort(state.sdkClient, 'warn', 'runtime', 'route_inactive', '小游戏宿主路由已失效', {
        reason: String((event && event.payload && event.payload.reason) || 'inactive')
      });
    }
    updateControls();
  }

  function syncPageLocale() {
    var language = String(
      (window.i18n && window.i18n.language)
      || document.documentElement.lang
      || 'zh-CN'
    ).trim();
    if (!language || language === state.locale) return;
    state.locale = language;
    updateControls();
    setPhase(state.phase);
    syncBrushToolButton();
  }

  function handleSdkVoiceState(voiceState) {
    var detail = voiceState && typeof voiceState === 'object' ? voiceState : {};
    if (detail.route_active === false) state.voiceRouteActive = false;
    if (typeof detail.active === 'boolean') state.voiceRouteActive = detail.active;
    updateControls();
  }

  function handleSdkVoiceTranscript(transcript) {
    if (!state.routeActive || state.routeEnding) return;
    var text = String((transcript && transcript.text) || '').trim().slice(0, 2000);
    if (!text) return;
    var requestId = String((transcript && transcript.requestId) || '').trim();
    var transcriptKey = requestId || [
      String((transcript && transcript.source) || 'sdk_voice_input'),
      String((transcript && transcript.timestamp) || ''),
      text
    ].join(':');
    if (transcriptKey === state.lastVoiceTranscriptRequestId) return;
    state.lastVoiceTranscriptRequestId = transcriptKey;
    submitPlayerText(text, {
      inputMetadata: {
        input_kind: 'user-voice',
        source: 'sdk_voice_input',
        request_id: requestId
      }
    });
  }

  function handleSdkVoiceError(payload) {
    updateControls();
    if (!state.routeActive || state.routeEnding) return;
    var error = payload && payload.error ? payload.error : payload;
    // Request-correlated failures also resolve/reject the matching voice API
    // call, where the initiating UI path reports them once. This event handler
    // is for asynchronous recognizer/bridge failures after that request ended.
    if (String((error && (error.request_id || error.requestId)) || '').trim()) return;
    addEventMessage('drawingGuess.voice.controlFailed', 'Voice operation failed: {{reason}}', {
      reason: sdkErrorReason(error)
    });
  }

  function querySdkVoiceRouteState(client) {
    var observedRequestSequence = state.voiceControlRequestSequence;
    if (!client || !client.capabilities.has('voice-input') || !isSdkRouteRunning(client)) {
      state.voiceRouteActive = false;
      updateControls();
      return Promise.resolve(false);
    }
    return client.voice.query({ timeoutMs: 5000 }).then(function (voiceState) {
      if (observedRequestSequence !== state.voiceControlRequestSequence) return false;
      handleSdkVoiceState(voiceState);
      return true;
    }).catch(function () {
      if (observedRequestSequence !== state.voiceControlRequestSequence) return false;
      state.voiceRouteActive = false;
      updateControls();
      return false;
    });
  }

  function stopSdkVoiceBestEffort(client) {
    if (!client || !client.capabilities.has('voice-input')) return Promise.resolve(false);
    if (['running', 'degraded'].indexOf(client.runtime.state) < 0) return Promise.resolve(false);
    var stopRequest;
    try {
      stopRequest = client.voice.stop({ timeoutMs: 6500 });
    } catch (error) {
      stopRequest = Promise.reject(error);
    }
    return Promise.resolve(stopRequest).then(function (voiceState) {
      handleSdkVoiceState(voiceState);
      return !!voiceState && voiceState.ok !== false;
    }).catch(function () {
      state.voiceRouteActive = false;
      state.voiceControlPending = false;
      updateControls();
      return false;
    });
  }

  function handleSdkPageExit() {
    var client = state.sdkClient;
    if (client && !client.disposed && client.capabilities.has('voice-input')) {
      try {
        // The SDK dispatches page-exit handlers synchronously before it sends
        // the route-end beacon and disposes the bridge. Posting stop here gives
        // the main-window microphone owner an immediate, route-bound teardown
        // request even when no Promise continuation can run during unload.
        client.voice.stop({ timeoutMs: 6500 }).catch(function () {});
      } catch (_) { /* page-exit cleanup remains best effort */ }
    }
    cleanupRouteResources();
  }

  function connectMiniGameSdk() {
    if (state.sdkClient) return Promise.resolve(state.sdkClient);
    if (state.sdkConnectPromise) return state.sdkConnectPromise;
    if (!window.NekoMiniGame || !window.nekoMiniGameSameOriginHostReady) {
      return Promise.reject(new Error('minigame_sdk_unavailable'));
    }
    state.sdkConnectPromise = Promise.resolve(window.nekoMiniGameSameOriginHostReady)
      .then(function (createHost) {
        var transport = createHost({
          gameType: SDK_GAME_ID,
          gameVersion: SDK_GAME_VERSION,
          source: 'drawing_guess',
          displayName: 'Drawing Guess',
          sessionId: state.sessionId
        });
        return window.NekoMiniGame.connect({
          id: SDK_GAME_ID,
          version: SDK_GAME_VERSION,
          protocolVersion: '1',
          requiredCapabilities: ['runtime', 'logging', 'speech-output', 'avatar-renderer', 'memory'],
          optionalCapabilities: ['voice-input', 'storage'],
          contracts: {
            commands: ROUND_COMMAND_CONTRACTS
          }
        }, { transport: transport });
      })
      .then(function (client) {
        state.sdkClient = client;
        state.sessionId = client.runtime.session.id || state.sessionId;
        syncPageLocale();
        hydrateSdkPreferences(client).catch(function () {});
        client.logger.configure({
          captureGlobalErrors: false,
          contextProvider: function () {
            return { sessionId: state.sessionId, lanlanName: state.lanlanName };
          }
        });
        client.runtime.configure({
          payload: sdkRuntimePayload,
          heartbeat: { intervalMs: 2500, timeoutMs: 5000 },
          outputs: false,
          pageExit: {
            payload: function (context) {
              return sdkRouteEndPayload({ reason: String((context && context.type) || 'page-exit') });
            }
          }
        });
        state.sdkStateUnsubscribe = client.events.on('runtime-state', handleSdkRuntimeState);
        state.sdkInactiveUnsubscribe = client.events.on('runtime-inactive', handleSdkRuntimeInactive);
        state.sdkPageExitUnsubscribe = client.events.on('page-exit', handleSdkPageExit);
        state.sdkSpeechStateUnsubscribe = client.speech.onState(handleSpeechPlaybackState);
        state.sdkSpeechErrorUnsubscribe = client.speech.onError(function (error) {
          logSdkBestEffort(client, 'warn', 'speech', 'playback_bridge_error', '小游戏 SDK 播放状态桥异常', {
            reason: String((error && (error.code || error.message)) || 'unknown')
          });
        });
        if (client.capabilities.has('voice-input')) {
          state.sdkVoiceStateUnsubscribe = client.voice.onState(handleSdkVoiceState);
          state.sdkVoiceTranscriptUnsubscribe = client.voice.onTranscript(handleSdkVoiceTranscript);
          state.sdkVoiceErrorUnsubscribe = client.voice.onError(handleSdkVoiceError);
        }
        updateControls();
        return client;
      })
      .catch(function (error) {
        state.sdkConnectPromise = null;
        throw error;
      });
    return state.sdkConnectPromise;
  }

  function pulseRouteState(forceCanvas) {
    if (!state.routeActive || state.routeEnding) return Promise.resolve(false);
    if (forceCanvas) state.sdkPulseForceRequestedSequence += 1;
    if (state.sdkPulsePromise) return state.sdkPulsePromise;
    var pulseSessionId = state.sessionId;
    var pulseGeneration = state.sdkPulseGeneration;
    var pulsePromise;

    function samePulseOwner() {
      return state.sessionId === pulseSessionId && state.sdkPulseGeneration === pulseGeneration;
    }

    function finishPulse(result) {
      // Clear synchronously in the final network continuation. Using only a
      // `.finally()` leaves a microtask gap where a new force request can attach
      // to an already-settled promise and never receive its own delivery.
      if (state.sdkPulsePromise === pulsePromise) state.sdkPulsePromise = null;
      return result;
    }

    function runPulse() {
      var targetForceSequence = state.sdkPulseForceRequestedSequence;
      var forceThisPulse = targetForceSequence > state.sdkPulseForceAcknowledgedSequence;
      var previousCanvasHash = state.canvasContextLastHash;
      var previousCanvasSentAt = state.canvasContextLastSentAt;
      var previousClearAttemptAt = state.canvasContextLastClearAttemptAt;
      var expectedCanvas = shouldShareCanvasContext();
      var attemptedCanvasKind = '';
      return connectMiniGameSdk().then(function (client) {
        // runtime.pulse() invokes its configured payload factory synchronously.
        // Keep force scoped to that call so ordinary SDK heartbeats cannot keep
        // resending a JPEG merely because an earlier explicit pulse failed.
        state.sdkPulsePayloadForce = forceThisPulse;
        try {
          var request = client.runtime.pulse(true);
          attemptedCanvasKind = state.canvasContextLastPayloadKind;
          return request;
        } finally {
          state.sdkPulsePayloadForce = false;
        }
      }).then(function (response) {
        var pulseData = sdkResponseData(response);
        var ok = !!(response && response.ok !== false && pulseData.ok !== false && pulseData.active !== false);
        var ownerCurrent = samePulseOwner();
        var delivered = ownerCurrent && ok && (!forceThisPulse || !!attemptedCanvasKind || !expectedCanvas);
        if (ownerCurrent && ok && attemptedCanvasKind === 'clear') {
          state.canvasContextLastHash = '';
          state.canvasContextLastSentAt = 0;
          state.canvasContextLastClearAttemptAt = 0;
        }
        if (ownerCurrent && delivered && forceThisPulse) {
          state.sdkPulseForceAcknowledgedSequence = Math.max(
            state.sdkPulseForceAcknowledgedSequence,
            targetForceSequence
          );
        }
        if (ownerCurrent && !ok && (forceThisPulse || attemptedCanvasKind)) {
          state.canvasContextLastHash = previousCanvasHash;
          state.canvasContextLastSentAt = previousCanvasSentAt;
          state.canvasContextLastClearAttemptAt = previousClearAttemptAt;
        }
        if (ownerCurrent && state.sdkPulseForceRequestedSequence > targetForceSequence) {
          return runPulse();
        }
        return finishPulse(delivered);
      }).catch(function () {
        var ownerCurrent = samePulseOwner();
        if (ownerCurrent && (forceThisPulse || attemptedCanvasKind)) {
          state.canvasContextLastHash = previousCanvasHash;
          state.canvasContextLastSentAt = previousCanvasSentAt;
          state.canvasContextLastClearAttemptAt = previousClearAttemptAt;
        }
        if (ownerCurrent && state.sdkPulseForceRequestedSequence > targetForceSequence) {
          return runPulse();
        }
        return finishPulse(false);
      });
    }

    pulsePromise = runPulse();
    state.sdkPulsePromise = pulsePromise;
    return pulsePromise;
  }

  function pushCanvasContextForRoute(force) {
    return pulseRouteState(!!force);
  }

  function publishFinalSummaryRouteState() {
    return pulseRouteState(false);
  }

  function roundCommandPayload(extra) {
    return Object.assign({
      client_round_token: state.activeRoundToken != null ? state.activeRoundToken : state.roundFlowToken
    }, extra || {});
  }

  function hideAllStageViews() {
    state.aiDrawingAnimationToken += 1;
    clearAiDrawingPlaceholderHint(false);
    hideSizePreview();
    if (els.canvasStage) els.canvasStage.classList.remove('is-user-canvas');
    els.placeholder.classList.add('dg-hidden');
    els.drawPick.classList.add('dg-hidden');
    els.aiDrawing.classList.add('dg-hidden');
    els.canvas.classList.add('dg-hidden');
    els.summary.classList.add('dg-hidden');
  }

  function showPlaceholder() {
    stopCountdown();
    stopDrawPickAnimation();
    stopAiGuessSchedule();
    hideAllStageViews();
    clearAiDrawingPlaceholderHint(true);
    els.placeholder.classList.remove('dg-hidden');
    setBadge('');
  }

  function renderDrawPickOptions(options) {
    var cardsEl = els.drawPick.querySelector('.dg-draw-pick-cards');
    if (!cardsEl) return;
    cardsEl.innerHTML = '<span class="dg-pick-deck"></span>' + (options || []).map(function (option, index) {
      return '<button class="dg-pick-card dg-pick-card-' + index + ' dg-pick-option" type="button" data-word-id="' + escapeAttr(option.id) + '">'
        + '<span class="dg-pick-card-inner">'
        + '<span class="dg-pick-face dg-pick-face-back">?</span>'
        + '<span class="dg-pick-face dg-pick-face-front">' + escapeHtml(option.label || option.id || '?') + '</span>'
        + '</span>'
        + '</button>';
    }).join('');
    Array.prototype.slice.call(cardsEl.querySelectorAll('.dg-pick-option')).forEach(function (button) {
      button.addEventListener('click', function () {
        chooseUserDrawWord(button.getAttribute('data-word-id') || '');
      });
    });
  }

  function showDrawPickAnimation(options, seconds) {
    stopCountdown();
    stopDrawPickAnimation();
    state.drawPickOptions = (Array.isArray(options) ? options : (options ? [options] : [])).filter(function (option) {
      return option && option.id;
    });
    state.drawPickSeconds = seconds || ROUND_FALLBACK_SECONDS;
    hideAllStageViews();
    setPhase('drawing_pick');
    setBadge(t('drawingGuess.messages.drawingPickTitle', 'Drawing a word'));
    els.drawPick.querySelector('.dg-draw-pick-title').textContent = t('drawingGuess.messages.drawingPickTitle', 'Drawing a word');
    els.drawPick.querySelector('.dg-draw-pick-subtitle').textContent = t('drawingGuess.messages.drawingPickSubtitle', 'Hold on. She is drawing a few cards from the deck.');
    els.drawPick.querySelector('.dg-draw-pick-reveal').textContent = '';
    renderDrawPickOptions(state.drawPickOptions);
    els.drawPick.classList.remove('dg-draw-pick-spread', 'dg-draw-pick-ready', 'dg-draw-pick-revealed');
    els.drawPick.classList.add('dg-draw-pick-dealing');
    els.drawPick.classList.remove('dg-hidden');
    state.drawPickRevealTimer = setTimeout(function () {
      state.drawPickRevealTimer = null;
      els.drawPick.classList.remove('dg-draw-pick-dealing');
      els.drawPick.classList.add('dg-draw-pick-spread');
      state.drawPickTimer = setTimeout(function () {
        state.drawPickTimer = null;
        els.drawPick.classList.add('dg-draw-pick-revealed');
        state.drawPickRevealTimer = setTimeout(function () {
          state.drawPickRevealTimer = null;
          els.drawPick.classList.remove('dg-draw-pick-spread');
          els.drawPick.classList.add('dg-draw-pick-ready');
          els.drawPick.querySelector('.dg-draw-pick-reveal').textContent = t('drawingGuess.messages.drawingPickReady', 'Pick one card to draw.');
        }, 560);
      }, 180);
    }, DRAW_PICK_DURATION_MS);
  }

  function aiDrawingPlanNumber(value, minimum, maximum) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return null;
    if (value < minimum || value > maximum) return null;
    return value;
  }

  function aiDrawingPlanNumberText(value) {
    return String(Number(Number(value).toFixed(2)));
  }

  function aiDrawingPlanColor(value, fallback, allowNone) {
    var color = String(value == null ? '' : value).trim().toLowerCase();
    if (allowNone && (color === 'none' || color === 'transparent')) return 'none';
    if (/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/.test(color)) return color;
    return fallback;
  }

  function isAiDrawingPlanColor(value) {
    if (typeof value !== 'string') return false;
    var color = String(value == null ? '' : value).trim().toLowerCase();
    return color === 'none'
      || color === 'transparent'
      || /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/.test(color);
  }

  function aiDrawingPlanHasPaint(value) {
    return String(value || '').toLowerCase() !== 'none';
  }

  function normalizeAiDrawingPlanPoints(value, maximum, width, height, margin) {
    if (!Array.isArray(value) || value.length > maximum) return [];
    var points = [];
    for (var index = 0; index < value.length; index += 1) {
      var pair = value[index];
      if (!Array.isArray(pair) || pair.length !== 2) return [];
      var x = aiDrawingPlanNumber(pair[0], margin, width - margin);
      var y = aiDrawingPlanNumber(pair[1], margin, height - margin);
      if (x == null || y == null) return [];
      points.push([x, y]);
    }
    return points;
  }

  function normalizeAiDrawingPlanPath(value, width, height) {
    if (typeof value !== 'string') return '';
    var path = value.trim();
    if (!path || path.length > AI_DRAW_PLAN_MAX_PATH_CHARS || !/^[MLHVCSQTAZ0-9,.\-+\s]+$/.test(path)) return '';
    var tokens = path.match(/[MLHVCSQTAZ]|[-+]?(?:\d+(?:\.\d+)?|\.\d+)/g) || [];
    if (!tokens.length || tokens[0] !== 'M' || tokens.join('') !== path.replace(/[\s,]+/g, '')) return '';
    var arities = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0 };
    var index = 0;
    var commandCount = 0;
    var hasVisibleSegment = false;
    var segments = [];
    while (index < tokens.length) {
      var command = tokens[index];
      if (!Object.prototype.hasOwnProperty.call(arities, command)) return '';
      var arity = arities[command];
      var values = tokens.slice(index + 1, index + 1 + arity);
      if (values.length !== arity || values.some(function (token) {
        return Object.prototype.hasOwnProperty.call(arities, token);
      })) return '';
      index += arity + 1;
      commandCount += 1;
      if (commandCount > AI_DRAW_PLAN_MAX_PATH_COMMANDS) return '';
      if (command !== 'M' && command !== 'Z') hasVisibleSegment = true;

      var normalizedValues = [];
      for (var valueIndex = 0; valueIndex < values.length; valueIndex += 1) {
        var rawNumber = Number(values[valueIndex]);
        var normalized = null;
        if (['M', 'L', 'T', 'C', 'S', 'Q'].indexOf(command) >= 0) {
          normalized = aiDrawingPlanNumber(rawNumber, 0, valueIndex % 2 === 0 ? width : height);
        } else if (command === 'H') {
          normalized = aiDrawingPlanNumber(rawNumber, 0, width);
        } else if (command === 'V') {
          normalized = aiDrawingPlanNumber(rawNumber, 0, height);
        } else if (command === 'A' && (valueIndex === 0 || valueIndex === 1)) {
          normalized = aiDrawingPlanNumber(rawNumber, 0, valueIndex === 0 ? width : height);
        } else if (command === 'A' && valueIndex === 2) {
          normalized = aiDrawingPlanNumber(rawNumber, -360, 360);
        } else if (command === 'A' && (valueIndex === 3 || valueIndex === 4)) {
          normalized = aiDrawingPlanNumber(rawNumber, 0, 1);
          if (normalized !== 0 && normalized !== 1) return '';
        } else {
          normalized = aiDrawingPlanNumber(rawNumber, 0, valueIndex === 5 ? width : height);
        }
        if (normalized == null) return '';
        normalizedValues.push(aiDrawingPlanNumberText(normalized));
      }
      segments.push(command + (normalizedValues.length ? ' ' + normalizedValues.join(' ') : ''));
    }
    return hasVisibleSegment ? segments.join(' ') : '';
  }

  function normalizeAiDrawingPlanElement(value, width, height, remainingPoints) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    if (typeof value.type !== 'string') return null;
    var type = String(value.type || '').trim().toLowerCase();
    if (['line', 'polyline', 'polygon', 'rect', 'circle', 'ellipse', 'path'].indexOf(type) < 0) return null;
    var geometryKeys = {
      line: ['x1', 'y1', 'x2', 'y2'],
      polyline: ['points'],
      polygon: ['points'],
      rect: ['x', 'y', 'width', 'height', 'rx', 'ry'],
      circle: ['cx', 'cy', 'r'],
      ellipse: ['cx', 'cy', 'rx', 'ry'],
      path: ['d']
    }[type];
    var allowedKeys = ['type', 'stroke', 'fill', 'stroke_width', 'line_cap', 'line_join'].concat(geometryKeys);
    if (Object.keys(value).some(function (key) { return allowedKeys.indexOf(key) < 0; })) return null;
    if (value.stroke != null && !isAiDrawingPlanColor(value.stroke)) return null;
    if (value.fill != null && !isAiDrawingPlanColor(value.fill)) return null;
    if (value.line_cap != null && typeof value.line_cap !== 'string') return null;
    if (value.line_join != null && typeof value.line_join !== 'string') return null;
    var stroke = aiDrawingPlanColor(value.stroke == null ? '#2f3b45' : value.stroke, '', true);
    var fill = aiDrawingPlanColor(value.fill == null ? 'none' : value.fill, '', true);
    if ((type === 'line' || type === 'polyline')
      && (aiDrawingPlanHasPaint(fill) || !aiDrawingPlanHasPaint(stroke))) return null;
    if (!aiDrawingPlanHasPaint(stroke) && !aiDrawingPlanHasPaint(fill)) return null;
    var strokeWidth = aiDrawingPlanNumber(value.stroke_width == null ? 4 : value.stroke_width, 0.5, 32);
    if (strokeWidth == null) return null;
    var rawLineCap = String(value.line_cap == null ? 'round' : value.line_cap).trim().toLowerCase();
    var rawLineJoin = String(value.line_join == null ? 'round' : value.line_join).trim().toLowerCase();
    if (['butt', 'round', 'square'].indexOf(rawLineCap) < 0) return null;
    if (['bevel', 'round', 'miter'].indexOf(rawLineJoin) < 0) return null;
    var margin = aiDrawingPlanHasPaint(stroke) ? strokeWidth / 2 : 0;
    var normalized = {
      type: type,
      stroke: stroke,
      fill: fill,
      stroke_width: strokeWidth,
      line_cap: rawLineCap,
      line_join: rawLineJoin,
      point_count: 0
    };

    if (type === 'path') {
      normalized.d = normalizeAiDrawingPlanPath(value.d, width, height);
      return normalized.d ? normalized : null;
    }

    if (type === 'line') {
      normalized.x1 = aiDrawingPlanNumber(value.x1, margin, width - margin);
      normalized.y1 = aiDrawingPlanNumber(value.y1, margin, height - margin);
      normalized.x2 = aiDrawingPlanNumber(value.x2, margin, width - margin);
      normalized.y2 = aiDrawingPlanNumber(value.y2, margin, height - margin);
      if ([normalized.x1, normalized.y1, normalized.x2, normalized.y2].some(function (item) { return item == null; })) return null;
      return normalized;
    }

    if (type === 'polyline' || type === 'polygon') {
      var minimumPoints = type === 'polygon' ? 3 : 2;
      normalized.points = normalizeAiDrawingPlanPoints(
        value.points,
        AI_DRAW_PLAN_MAX_POINTS_PER_ELEMENT,
        width,
        height,
        margin
      );
      normalized.point_count = normalized.points.length;
      return normalized.points.length >= minimumPoints && normalized.point_count <= remainingPoints ? normalized : null;
    }

    if (type === 'rect') {
      normalized.x = aiDrawingPlanNumber(value.x, margin, width - margin);
      normalized.y = aiDrawingPlanNumber(value.y, margin, height - margin);
      if (normalized.x == null || normalized.y == null) return null;
      normalized.width = aiDrawingPlanNumber(value.width, 0.5, width);
      normalized.height = aiDrawingPlanNumber(value.height, 0.5, height);
      if (normalized.width == null || normalized.height == null) return null;
      if (normalized.x + normalized.width + margin > width || normalized.y + normalized.height + margin > height) return null;
      if (value.rx != null && typeof value.rx !== 'number') return null;
      if (value.ry != null && typeof value.ry !== 'number') return null;
      normalized.rx = aiDrawingPlanNumber(value.rx == null ? 0 : value.rx, 0, normalized.width / 2);
      normalized.ry = aiDrawingPlanNumber(value.ry == null ? 0 : value.ry, 0, normalized.height / 2);
      if (normalized.rx == null || normalized.ry == null) return null;
      return normalized;
    }

    if (type === 'circle') {
      normalized.r = aiDrawingPlanNumber(value.r, 0.5, Math.min(width, height) / 2);
      if (normalized.r == null) return null;
      normalized.cx = aiDrawingPlanNumber(value.cx, normalized.r + margin, width - normalized.r - margin);
      normalized.cy = aiDrawingPlanNumber(value.cy, normalized.r + margin, height - normalized.r - margin);
      return normalized.cx != null && normalized.cy != null ? normalized : null;
    }
    normalized.rx = aiDrawingPlanNumber(value.rx, 0.5, width / 2);
    normalized.ry = aiDrawingPlanNumber(value.ry, 0.5, height / 2);
    if (normalized.rx == null || normalized.ry == null) return null;
    normalized.cx = aiDrawingPlanNumber(value.cx, normalized.rx + margin, width - normalized.rx - margin);
    normalized.cy = aiDrawingPlanNumber(value.cy, normalized.ry + margin, height - normalized.ry - margin);
    return normalized.cx != null && normalized.cy != null ? normalized : null;
  }

  function normalizeAiDrawingPlan(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    var topLevelKeys = Object.keys(value).sort().join(',');
    if (topLevelKeys !== 'background,elements,height,version,width') return null;
    var version = value.version;
    var width = value.width;
    var height = value.height;
    if (version !== 1 || width !== AI_DRAW_PLAN_WIDTH || height !== AI_DRAW_PLAN_HEIGHT) return null;
    if (String(value.background || '').trim().toLowerCase() !== '#fffdfa') return null;
    if (!Array.isArray(value.elements) || !value.elements.length || value.elements.length > AI_DRAW_PLAN_MAX_ELEMENTS) return null;
    var totalPoints = 0;
    var elements = [];
    for (var index = 0; index < value.elements.length; index += 1) {
      var normalized = normalizeAiDrawingPlanElement(
        value.elements[index],
        width,
        height,
        AI_DRAW_PLAN_MAX_TOTAL_POINTS - totalPoints
      );
      if (!normalized) return null;
      totalPoints += normalized.point_count;
      delete normalized.point_count;
      elements.push(normalized);
    }
    return {
      version: 1,
      width: width,
      height: height,
      background: '#fffdfa',
      elements: elements
    };
  }

  function paintAiDrawingPlanBackground(context, plan) {
    context.save();
    if (typeof context.setTransform === 'function') context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, plan.width, plan.height);
    context.fillStyle = plan.background;
    context.fillRect(0, 0, plan.width, plan.height);
    context.restore();
  }

  function traceAiDrawingPlanRect(context, element) {
    var radiusX = Math.max(0, Math.min(element.rx || 0, element.width / 2));
    var radiusY = Math.max(0, Math.min(element.ry || 0, element.height / 2));
    if (!radiusX || !radiusY) {
      context.rect(element.x, element.y, element.width, element.height);
      return;
    }
    context.moveTo(element.x + radiusX, element.y);
    context.lineTo(element.x + element.width - radiusX, element.y);
    context.quadraticCurveTo(element.x + element.width, element.y, element.x + element.width, element.y + radiusY);
    context.lineTo(element.x + element.width, element.y + element.height - radiusY);
    context.quadraticCurveTo(element.x + element.width, element.y + element.height, element.x + element.width - radiusX, element.y + element.height);
    context.lineTo(element.x + radiusX, element.y + element.height);
    context.quadraticCurveTo(element.x, element.y + element.height, element.x, element.y + element.height - radiusY);
    context.lineTo(element.x, element.y + radiusY);
    context.quadraticCurveTo(element.x, element.y, element.x + radiusX, element.y);
    context.closePath();
  }

  function paintAiDrawingPlanElement(context, element) {
    context.save();
    context.beginPath();
    context.lineWidth = element.stroke_width;
    context.lineCap = element.line_cap;
    context.lineJoin = element.line_join;
    context.strokeStyle = aiDrawingPlanHasPaint(element.stroke) ? element.stroke : 'rgba(0,0,0,0)';
    context.fillStyle = aiDrawingPlanHasPaint(element.fill) ? element.fill : 'rgba(0,0,0,0)';
    var path = null;
    if (element.type === 'path') {
      if (typeof window.Path2D !== 'function') {
        context.restore();
        return false;
      }
      try {
        path = new window.Path2D(element.d);
      } catch (_) {
        context.restore();
        return false;
      }
    } else if (element.type === 'line') {
      context.moveTo(element.x1, element.y1);
      context.lineTo(element.x2, element.y2);
    } else if (element.type === 'polyline' || element.type === 'polygon') {
      context.moveTo(element.points[0][0], element.points[0][1]);
      element.points.slice(1).forEach(function (point) {
        context.lineTo(point[0], point[1]);
      });
      if (element.type === 'polygon') context.closePath();
    } else if (element.type === 'rect') {
      traceAiDrawingPlanRect(context, element);
    } else if (element.type === 'circle') {
      context.arc(element.cx, element.cy, element.r, 0, Math.PI * 2);
    } else if (element.type === 'ellipse') {
      context.ellipse(element.cx, element.cy, element.rx, element.ry, 0, 0, Math.PI * 2);
    }
    if (aiDrawingPlanHasPaint(element.fill)) {
      if (path) context.fill(path);
      else context.fill();
    }
    if (aiDrawingPlanHasPaint(element.stroke)) {
      if (path) context.stroke(path);
      else context.stroke();
    }
    context.restore();
    return true;
  }

  function renderAiDrawingPlanToCanvas(value, canvas, elementLimit) {
    var plan = normalizeAiDrawingPlan(value);
    if (!plan || !canvas || typeof canvas.getContext !== 'function') return false;
    var context = canvas.getContext('2d');
    if (!context) return false;
    canvas.width = plan.width;
    canvas.height = plan.height;
    paintAiDrawingPlanBackground(context, plan);
    var limit = elementLimit == null
      ? plan.elements.length
      : Math.max(0, Math.min(plan.elements.length, Number(elementLimit) || 0));
    for (var index = 0; index < limit; index += 1) {
      if (!paintAiDrawingPlanElement(context, plan.elements[index])) return false;
    }
    return true;
  }

  function aiDrawingPlanToSvg(value) {
    var plan = normalizeAiDrawingPlan(value);
    if (!plan || !document.createElementNS) return '';
    try {
      var namespace = 'http://www.w3.org/2000/svg';
      var svg = document.createElementNS(namespace, 'svg');
      svg.setAttribute('viewBox', '0 0 ' + plan.width + ' ' + plan.height);
      var background = document.createElementNS(namespace, 'rect');
      background.setAttribute('x', '0');
      background.setAttribute('y', '0');
      background.setAttribute('width', String(plan.width));
      background.setAttribute('height', String(plan.height));
      background.setAttribute('fill', plan.background);
      svg.appendChild(background);
      plan.elements.forEach(function (element) {
        var node = document.createElementNS(namespace, element.type);
        if (element.type === 'path') {
          node.setAttribute('d', element.d);
        } else if (element.type === 'line') {
          ['x1', 'y1', 'x2', 'y2'].forEach(function (key) {
            node.setAttribute(key, aiDrawingPlanNumberText(element[key]));
          });
        } else if (element.type === 'polyline' || element.type === 'polygon') {
          node.setAttribute('points', element.points.map(function (point) {
            return aiDrawingPlanNumberText(point[0]) + ',' + aiDrawingPlanNumberText(point[1]);
          }).join(' '));
        } else if (element.type === 'rect') {
          ['x', 'y', 'width', 'height'].forEach(function (key) {
            node.setAttribute(key, aiDrawingPlanNumberText(element[key]));
          });
          if (element.rx) node.setAttribute('rx', aiDrawingPlanNumberText(element.rx));
          if (element.ry) node.setAttribute('ry', aiDrawingPlanNumberText(element.ry));
        } else if (element.type === 'circle') {
          ['cx', 'cy', 'r'].forEach(function (key) {
            node.setAttribute(key, aiDrawingPlanNumberText(element[key]));
          });
        } else if (element.type === 'ellipse') {
          ['cx', 'cy', 'rx', 'ry'].forEach(function (key) {
            node.setAttribute(key, aiDrawingPlanNumberText(element[key]));
          });
        }
        node.setAttribute('fill', element.fill || 'none');
        node.setAttribute('stroke', element.stroke || 'none');
        node.setAttribute('stroke-width', aiDrawingPlanNumberText(element.stroke_width));
        node.setAttribute('stroke-linecap', element.line_cap);
        node.setAttribute('stroke-linejoin', element.line_join);
        svg.appendChild(node);
      });
      try {
        return new XMLSerializer().serializeToString(svg);
      } catch (_) {
        return svg.outerHTML || '';
      }
    } catch (_) {
      return '';
    }
  }

  function captureAiDrawingReviewImage(value) {
    var plan = normalizeAiDrawingPlan(value);
    if (!plan) return '';
    try {
      var source = document.createElement('canvas');
      if (!renderAiDrawingPlanToCanvas(plan, source)) return '';
      var review = document.createElement('canvas');
      review.width = AI_DRAW_REVIEW_WIDTH;
      review.height = AI_DRAW_REVIEW_HEIGHT;
      var context = review.getContext('2d');
      if (!context) return '';
      context.fillStyle = plan.background;
      context.fillRect(0, 0, review.width, review.height);
      context.drawImage(source, 0, 0, review.width, review.height);
      var dataUrl = review.toDataURL('image/jpeg', 0.78);
      return dataUrl.length <= VISION_COMMAND_DATA_MAX_CHARS ? dataUrl : '';
    } catch (_) {
      return '';
    }
  }

  function animateAiDrawingPlan(canvas, value) {
    var plan = normalizeAiDrawingPlan(value);
    if (!plan || !canvas || typeof canvas.getContext !== 'function') return;
    var context = canvas.getContext('2d');
    if (!context) return;
    var reduceMotion = false;
    try {
      reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (_) {}
    if (reduceMotion) {
      renderAiDrawingPlanToCanvas(plan, canvas);
      return;
    }
    canvas.width = plan.width;
    canvas.height = plan.height;
    paintAiDrawingPlanBackground(context, plan);
    var animationToken = ++state.aiDrawingAnimationToken;
    var startedAt = null;
    var rendered = 0;
    function frame(timestamp) {
      if (animationToken !== state.aiDrawingAnimationToken) return;
      var now = Number.isFinite(timestamp) ? timestamp : Date.now();
      if (startedAt == null) startedAt = now;
      var target = Math.min(plan.elements.length, Math.max(1, Math.floor((now - startedAt) / 45) + 1));
      while (rendered < target) {
        paintAiDrawingPlanElement(context, plan.elements[rendered]);
        rendered += 1;
      }
      if (rendered < plan.elements.length) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function showAiDrawingPlan(value) {
    var plan = normalizeAiDrawingPlan(value);
    if (!plan) return false;
    try {
      hideAllStageViews();
      els.aiDrawing.innerHTML = '';
      var canvas = document.createElement('canvas');
      canvas.className = 'dg-ai-plan-canvas';
      canvas.width = plan.width;
      canvas.height = plan.height;
      canvas.setAttribute('aria-hidden', 'true');
      if (!renderAiDrawingPlanToCanvas(plan, canvas, 0)) return false;
      els.aiDrawing.appendChild(canvas);
      state.aiDrawingPlan = plan;
      state.aiSvg = aiDrawingPlanToSvg(plan) || state.aiSvg;
      els.aiDrawing.style.visibility = '';
      els.aiDrawing.classList.remove('dg-hidden');
      animateAiDrawingPlan(canvas, plan);
      return true;
    } catch (_) {
      return false;
    }
  }

  function aiDrawingPlanFromResponse(response) {
    if (!response || typeof response !== 'object') return null;
    var drawing = response.drawing && typeof response.drawing === 'object' ? response.drawing : {};
    return normalizeAiDrawingPlan(
      drawing.plan || drawing.drawing_plan || response.plan || response.drawing_plan || response.corrected_plan
    );
  }

  function aiDrawingPlanSignature(value) {
    var plan = normalizeAiDrawingPlan(value);
    if (!plan) return '';
    try {
      return JSON.stringify(plan);
    } catch (_) {
      return '';
    }
  }

  function prepareAiDrawing(drawing, flowToken) {
    drawing = drawing && typeof drawing === 'object' ? drawing : {};
    var originalPlan = normalizeAiDrawingPlan(drawing.plan || drawing.drawing_plan);
    var prepared = {
      plan: originalPlan,
      svg: originalPlan ? aiDrawingPlanToSvg(originalPlan) : String(drawing.svg || '')
    };
    if (!originalPlan) return Promise.resolve(prepared);
    var reviewImage = captureAiDrawingReviewImage(originalPlan);
    if (!reviewImage) return Promise.resolve(prepared);
    return executeRoundCommand(ROUND_COMMANDS.AI_DRAW_REVIEW, roundCommandPayload({
      image_data_url: reviewImage
    }), AI_DRAW_REVIEW_REQUEST_TIMEOUT_MS).then(function (response) {
      ensureCurrentRoundFlow(flowToken);
      if (!response || response.ok === false || response.handled === false || response.skipped === true) return prepared;
      var reviewedPlan = aiDrawingPlanFromResponse(response);
      if (!reviewedPlan) return prepared;
      return {
        plan: reviewedPlan,
        svg: aiDrawingPlanToSvg(reviewedPlan) || prepared.svg
      };
    }).catch(function (error) {
      if (!isCurrentRoundFlow(flowToken) || (error && error.staleRoundFlow)) throw staleRoundFlowError();
      return prepared;
    });
  }

  function reviewAiDrawingInBackground(drawing, flowToken) {
    prepareAiDrawing(drawing, flowToken).then(function (prepared) {
      if (!isCurrentRoundFlow(flowToken) || state.phase !== 'user_guessing') return;
      prepared = prepared || {};
      var reviewedPlan = normalizeAiDrawingPlan(prepared.plan);
      var reviewedSignature = aiDrawingPlanSignature(reviewedPlan);
      var visibleSignature = aiDrawingPlanSignature(state.aiDrawingPlan);
      if (reviewedSignature && reviewedSignature === visibleSignature) return;
      state.aiDrawingPlan = reviewedPlan;
      state.aiSvg = prepared.svg || (reviewedPlan ? aiDrawingPlanToSvg(reviewedPlan) : state.aiSvg);
      if (!state.aiDrawingPlan || !showAiDrawingPlan(state.aiDrawingPlan)) {
        showAiDrawing(state.aiSvg);
      }
    }).catch(function (error) {
      if (error && error.staleRoundFlow) return;
    });
  }

  function showAiDrawing(svgMarkup) {
    hideAllStageViews();
    els.aiDrawing.innerHTML = svgMarkup || '';
    var svg = els.aiDrawing.querySelector('svg');
    normalizeAiDrawingSvg(svg);
    els.aiDrawing.style.visibility = 'hidden';
    els.aiDrawing.classList.remove('dg-hidden');
    requestAnimationFrame(function () {
      fitAiDrawingSvgToContent(svg);
      state.aiSvg = serializeAiDrawingSvg(els.aiDrawing) || state.aiSvg;
      els.aiDrawing.style.visibility = '';
      animateAiDrawing(svg);
    });
  }

  function normalizeAiDrawingSvg(svg) {
    if (!svg) return;
    if (!svg.getAttribute('viewBox')) {
      svg.setAttribute('viewBox', '0 0 240 180');
    }
    svg.removeAttribute('width');
    svg.removeAttribute('height');
    // Match the Canvas renderer: both the generated drawing and the player's
    // 800 x 600 backing canvas fill the whole responsive stage.
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('focusable', 'false');
    svg.style.transform = '';
    svg.style.transformOrigin = '';
  }

  function parseSvgViewBox(svg) {
    var raw = String(svg.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
    if (raw.length === 4 && raw.every(function (value) { return Number.isFinite(value); }) && raw[2] > 0 && raw[3] > 0) {
      return raw;
    }
    return [0, 0, 240, 180];
  }

  function isFullCanvasRect(node, viewBox, screenRect, svgRect) {
    if (!node || String(node.tagName || '').toLowerCase() !== 'rect') return false;
    if (screenRect && svgRect) {
      return Math.abs(screenRect.left - svgRect.left) <= 4
        && Math.abs(screenRect.top - svgRect.top) <= 4
        && screenRect.width >= svgRect.width * 0.92
        && screenRect.height >= svgRect.height * 0.92;
    }
    var x = Number(node.getAttribute('x') || viewBox[0] || 0);
    var y = Number(node.getAttribute('y') || viewBox[1] || 0);
    var width = Number(node.getAttribute('width') || 0);
    var height = Number(node.getAttribute('height') || 0);
    return Math.abs(x - viewBox[0]) <= 1
      && Math.abs(y - viewBox[1]) <= 1
      && width >= viewBox[2] * 0.95
      && height >= viewBox[3] * 0.95;
  }

  function screenRectToSvgBounds(svg, rect) {
    if (!svg || !rect || !svg.getScreenCTM || !svg.createSVGPoint) return null;
    var matrix = svg.getScreenCTM();
    if (!matrix) return null;
    var inverse = null;
    try {
      inverse = matrix.inverse();
    } catch (_) {
      return null;
    }
    var point = svg.createSVGPoint();
    var corners = [
      [rect.left, rect.top],
      [rect.right, rect.top],
      [rect.right, rect.bottom],
      [rect.left, rect.bottom]
    ].map(function (pair) {
      point.x = pair[0];
      point.y = pair[1];
      return point.matrixTransform(inverse);
    });
    return corners.reduce(function (bounds, p) {
      if (!bounds) return { x1: p.x, y1: p.y, x2: p.x, y2: p.y };
      bounds.x1 = Math.min(bounds.x1, p.x);
      bounds.y1 = Math.min(bounds.y1, p.y);
      bounds.x2 = Math.max(bounds.x2, p.x);
      bounds.y2 = Math.max(bounds.y2, p.y);
      return bounds;
    }, null);
  }

  function mergeSvgBounds(bounds, nextBounds) {
    if (!nextBounds) return bounds;
    if (!bounds) {
      return {
        x1: nextBounds.x1,
        y1: nextBounds.y1,
        x2: nextBounds.x2,
        y2: nextBounds.y2
      };
    }
    bounds.x1 = Math.min(bounds.x1, nextBounds.x1);
    bounds.y1 = Math.min(bounds.y1, nextBounds.y1);
    bounds.x2 = Math.max(bounds.x2, nextBounds.x2);
    bounds.y2 = Math.max(bounds.y2, nextBounds.y2);
    return bounds;
  }

  function measureSvgContentMetrics(svg, viewBox) {
    var svgRect = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
    if (!svgRect || svgRect.width <= 0 || svgRect.height <= 0) return null;
    var metrics = Array.prototype.slice.call(svg.querySelectorAll('path,line,polyline,polygon,rect,circle,ellipse')).reduce(function (current, node) {
      var rect = null;
      try {
        rect = node.getBoundingClientRect();
      } catch (_) {}
      if (!rect || rect.width < 0.5 || rect.height < 0.5) return current;
      if (isFullCanvasRect(node, viewBox, rect, svgRect)) return current;
      var nodeBounds = screenRectToSvgBounds(svg, rect);
      if (!nodeBounds) return current;
      current.bounds = mergeSvgBounds(current.bounds, nodeBounds);
      var width = Math.max(0, nodeBounds.x2 - nodeBounds.x1);
      var height = Math.max(0, nodeBounds.y2 - nodeBounds.y1);
      var weight = Math.max(1, width * height);
      current.weight += weight;
      current.centerX += ((nodeBounds.x1 + nodeBounds.x2) / 2) * weight;
      current.centerY += ((nodeBounds.y1 + nodeBounds.y2) / 2) * weight;
      return current;
    }, { bounds: null, centerX: 0, centerY: 0, weight: 0 });
    if (!metrics.bounds) return null;
    return {
      bounds: metrics.bounds,
      centerX: metrics.weight > 0 ? metrics.centerX / metrics.weight : (metrics.bounds.x1 + metrics.bounds.x2) / 2,
      centerY: metrics.weight > 0 ? metrics.centerY / metrics.weight : (metrics.bounds.y1 + metrics.bounds.y2) / 2
    };
  }

  function clampCenterForBounds(center, minBound, maxBound, viewSize) {
    var contentSize = Math.max(1, maxBound - minBound);
    var margin = Math.max(0, Math.min((viewSize - contentSize) / 2, viewSize * 0.08));
    var minCenter = maxBound + margin - viewSize / 2;
    var maxCenter = minBound - margin + viewSize / 2;
    if (minCenter > maxCenter) return (minBound + maxBound) / 2;
    return Math.max(minCenter, Math.min(maxCenter, center));
  }

  function fitAiDrawingSvgToContent(svg) {
    if (!svg) return;
    var viewBox = parseSvgViewBox(svg);
    var stageRect = els.aiDrawing && typeof els.aiDrawing.getBoundingClientRect === 'function'
      ? els.aiDrawing.getBoundingClientRect()
      : null;
    var viewBoxRatio = stageRect && stageRect.width > 0 && stageRect.height > 0
      ? stageRect.width / stageRect.height
      : viewBox[2] / viewBox[3];
    var metrics = measureSvgContentMetrics(svg, viewBox);
    if (!metrics || !metrics.bounds) return;
    var bounds = metrics.bounds;
    var contentWidth = Math.max(1, bounds.x2 - bounds.x1);
    var contentHeight = Math.max(1, bounds.y2 - bounds.y1);
    var maxContentRatio = 0.62;
    var nextWidth = Math.max(contentWidth / maxContentRatio, (contentHeight / maxContentRatio) * viewBoxRatio);
    var nextHeight = nextWidth / viewBoxRatio;
    var centerX = clampCenterForBounds(metrics.centerX, bounds.x1, bounds.x2, nextWidth);
    var centerY = clampCenterForBounds(metrics.centerY, bounds.y1, bounds.y2, nextHeight);
    var nextX = centerX - nextWidth / 2;
    var nextY = centerY - nextHeight / 2;
    svg.setAttribute('viewBox', [nextX, nextY, nextWidth, nextHeight].map(function (value) {
      return Number(value.toFixed(2));
    }).join(' '));
  }

  function serializeAiDrawingSvg(container) {
    var svg = container && container.querySelector('svg');
    return svg ? svg.outerHTML : '';
  }

  function animateAiDrawing(svg) {
    if (!svg) return;
    try {
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        return;
      }
    } catch (_) {}
    var allowed = { g: true, path: true, line: true, polyline: true, polygon: true, rect: true, circle: true, ellipse: true };
    var items = Array.prototype.slice.call(svg.children).filter(function (node) {
      return node && allowed[String(node.tagName || '').toLowerCase()];
    });
    if (!items.length) return;
    items.forEach(function (node, index) {
      node.style.opacity = '0';
      node.style.transition = 'opacity 180ms ease ' + (index * 45) + 'ms';
      node.style.transform = '';
      node.style.transformOrigin = '';
    });
    requestAnimationFrame(function () {
      items.forEach(function (node) {
        node.style.opacity = '1';
      });
    });
  }

  function showCanvas() {
    hideAllStageViews();
    if (els.canvasStage) els.canvasStage.classList.add('is-user-canvas');
    els.canvas.classList.remove('dg-hidden');
  }

  function showSummary() {
    hideAllStageViews();
    els.summary.classList.remove('dg-hidden');
    setBadge('');
  }

  function addAiGuessOutcomeMessage(res) {
    if (!res || res.kind !== 'ai_guess') return;
    if (res.correct) {
      addEventMessage('drawingGuess.messages.aiGuessCorrect', 'She guessed it right.');
      return;
    }
    if (res.answer && res.answer.label) {
      addEventMessage('drawingGuess.messages.aiGuessMissAnswer', 'She missed it. Answer: {{answer}}', {
        answer: res.answer.label
      });
      return;
    }
    addEventMessage('drawingGuess.messages.aiGuessWrong', 'Not quite.');
  }

  function reconcileFailedSdkStart(client, reason) {
    if (!client || client.runtime.state !== 'degraded') return Promise.resolve(false);
    if (state.sdkReconcilePromise) return state.sdkReconcilePromise;
    // A transport timeout is ambiguous: the backend may already own the route
    // even though start() rejected locally. End with every unresolved SDK
    // generation before another start can mint more candidates.
    // Do not let an unsuccessful reconciliation heartbeat or poll an unknown
    // route forever. Keep only the SDK's page-exit listener so closing the
    // window can still make one last beacon attempt; a later Start click retries
    // this exact end before it may mint a new generation.
    try { client.runtime.startMonitoring({ heartbeat: false, outputs: false }); } catch (_) {}
    var reconcilePromise = client.runtime.end(sdkRouteEndPayload({
      reason: 'drawing_guess_sdk_start_reconcile_' + String(reason || 'failed'),
      completedRoute: false,
      suppressWindowStateChange: true,
      suppressRouteEndStatus: true
    }), { timeoutMs: 10000 }).then(function (response) {
      var data = sdkResponseData(response);
      var ended = !!response && response.ok !== false && data.ok !== false
        && client.runtime.state === 'ended';
      if (ended) cleanupRouteResources();
      else {
        try { client.runtime.startMonitoring({ heartbeat: false, outputs: false }); } catch (_) {}
      }
      return ended;
    }).catch(function () {
      try { client.runtime.startMonitoring({ heartbeat: false, outputs: false }); } catch (_) {}
      return false;
    }).finally(function () {
      if (state.sdkReconcilePromise === reconcilePromise) state.sdkReconcilePromise = null;
    });
    state.sdkReconcilePromise = reconcilePromise;
    return reconcilePromise;
  }

  function sdkStartRecoveryBlockedError() {
    var error = new Error('sdk_route_reconciliation_pending');
    error.code = 'sdk_route_reconciliation_pending';
    error.sdkRecoveryBlocked = true;
    return error;
  }

  function configureSdkMemoryConsent(client) {
    var requested = state.memoryConsent === 'summary';
    var consent = client.memory.consent;
    if (consent.locked) {
      if (consent.configured && consent.enabled === requested) return Promise.resolve(true);
      var lockedError = new Error('memory_consent_locked');
      lockedError.code = 'memory_consent_locked';
      return Promise.reject(lockedError);
    }
    if (consent.configured && consent.enabled === requested) return Promise.resolve(true);
    return client.memory.configureConsent(requested, { timeoutMs: 8000 }).then(function (response) {
      var data = sdkResponseData(response);
      if (!response || response.ok === false || data.ok === false || data.enabled !== requested) {
        var rejectedError = new Error('memory_consent_rejected');
        rejectedError.code = 'memory_consent_rejected';
        throw rejectedError;
      }
      return true;
    });
  }

  function startRoute() {
    if (!state.lanlanName) {
      setStatus('missingCharacter', 'Missing character');
      addMessage('drawingGuess.messages.missingCharacter', 'Open this page with a character or load the current one first.');
      updateControls();
      return Promise.resolve(false);
    }
    if (state.routeActive) return Promise.resolve(true);
    if (state.sdkStartPromise) return state.sdkStartPromise;
    state.routeEnding = false;
    setStatus('starting', 'Starting');
    updateControls();
    var startPromise = connectMiniGameSdk().then(function (client) {
      return configureSdkMemoryConsent(client).then(function () { return client; });
    }).then(function (client) {
      if (client.runtime.state !== 'degraded') {
        return client.runtime.start(routePayload(), { timeoutMs: 12000 });
      }
      // A previous ambiguous start/end failure must be reconciled before a new
      // generation is allowed. Otherwise every click can create another
      // unresolved backend route while the first route is still active.
      return reconcileFailedSdkStart(client, 'retry_before_start').then(function (reconciled) {
        if (!reconciled) throw sdkStartRecoveryBlockedError();
        return client.runtime.start(routePayload(), { timeoutMs: 12000 });
      });
    }).then(function (response) {
      var client = state.sdkClient;
      var res = sdkResponseData(response);
      if (!response || response.ok === false || res.ok === false || !client || client.runtime.state !== 'running') {
        var rejectedReason = (res && res.reason) || 'unknown';
        return reconcileFailedSdkStart(client, rejectedReason).then(function () {
          setStatus('failed', 'Start failed');
          addMessage('drawingGuess.messages.startFailed', 'Route start failed: {{reason}}', { reason: rejectedReason });
          return false;
        });
      }
      state.routeActive = true;
      state.voiceRouteActive = false;
      state.voiceControlPending = false;
      state.lastVoiceTranscriptRequestId = '';
      state.sessionId = client.runtime.session.id || state.sessionId;
      if (res.state && res.state.lanlan_name) state.lanlanName = String(res.state.lanlan_name || state.lanlanName);
      setStatus('active', 'Active');
      // Do not expose the active round until the session-scoped logging gate
      // has settled. The host bounds and aborts this enable request, preventing
      // a late /logs/enable from reactivating an already-ended session.
      return Promise.resolve(client.logger.enableAfterRuntimeStart()).then(function (logResult) {
        if (!isSdkRouteRunning(client)) return false;
        if (logResult && logResult.ok) {
          logSdkBestEffort(client, 'info', 'runtime', 'sdk_route_started', '你画我猜已通过小游戏 SDK 启动', {
            sdk_version: String(window.NekoMiniGame && window.NekoMiniGame.version || ''),
            host_version: String(client.host && client.host.version || ''),
            capabilities: client.capabilities.granted.slice()
          });
        }
        return true;
      }).catch(function () {
        return isSdkRouteRunning(client);
      }).then(function (started) {
        if (!started) return false;
        // The host owns the single microphone session. Reflect its current
        // state without starting a second recognizer or taking over capture.
        querySdkVoiceRouteState(client).catch(function () {});
        return true;
      });
    }).catch(function (error) {
      var failureReason = sdkErrorReason(error);
      if (error && error.sdkRecoveryBlocked) {
        setStatus('failed', 'Start failed');
        addMessage('drawingGuess.messages.startFailed', 'Route start failed: {{reason}}', { reason: failureReason });
        return false;
      }
      return reconcileFailedSdkStart(state.sdkClient, failureReason).then(function () {
        setStatus('failed', 'Start failed');
        addMessage('drawingGuess.messages.startFailed', 'Route start failed: {{reason}}', { reason: failureReason });
        return false;
      });
    }).finally(function () {
      stopThinkingEventMessage();
      if (state.sdkStartPromise === startPromise) state.sdkStartPromise = null;
      updateControls();
    });
    state.sdkStartPromise = startPromise;
    updateControls();
    return startPromise;
  }

  function endRoute(useBeacon, options) {
    options = options || {};
    if (!state.routeActive && !useBeacon) return Promise.resolve({ ok: true });
    state.routeEnding = true;
    setStatus('ending', 'Ending');
    updateControls();
    return connectMiniGameSdk().then(function (client) {
      logSdkBestEffort(client, 'info', 'runtime', 'sdk_route_ending', '你画我猜正在通过小游戏 SDK 结束', {
        completed: !!options.finalSummary || state.phase === 'summary' || state.phase === 'final_summary'
      });
      return stopSdkVoiceBestEffort(client).then(function () {
        return client.runtime.end(sdkRouteEndPayload(options), {
          timeoutMs: 12000,
          useBeacon: useBeacon === true
        });
      });
    }).then(function (response) {
      var data = sdkResponseData(response);
      var ended = !!response && response.ok !== false && data.ok !== false
        && state.sdkClient && state.sdkClient.runtime.state === 'ended';
      state.routeActive = !ended && !!state.sdkClient
        && ['running', 'degraded'].indexOf(state.sdkClient.runtime.state) >= 0;
      if (!ended) {
        setStatus(state.routeActive ? 'active' : 'failed', state.routeActive ? 'Active' : 'End failed');
        return response;
      }
      cleanupRouteResources();
      setStatus('ended', 'Ended');
      if (options.finalSummary) {
        renderFinalSummary();
      } else {
        setPhase('ended');
      }
      return response;
    }).catch(function (error) {
      state.routeActive = !!state.sdkClient
        && ['running', 'degraded'].indexOf(state.sdkClient.runtime.state) >= 0;
      setStatus(state.routeActive ? 'active' : 'failed', state.routeActive ? 'Active' : 'End failed');
      return { ok: false, reason: sdkErrorReason(error) };
    }).finally(function () {
      state.routeEnding = false;
      updateControls();
    });
  }

  function stopCountdown() {
    clearInterval(state.countdownTimer);
    state.countdownTimer = null;
  }

  function startCountdown(seconds, onDone) {
    stopCountdown();
    var remaining = Number(seconds || 0);
    function tick() {
      setBadge(t('drawingGuess.timer.seconds', '{{seconds}}s', { seconds: remaining }));
      if (remaining <= 0) {
        stopCountdown();
        onDone();
        return;
      }
      remaining -= 1;
    }
    tick();
    state.countdownTimer = setInterval(tick, 1000);
  }

  function aiGuessTimeRemainingMs() {
    return Math.max(0, Number(state.aiGuessDeadline || 0) - Date.now());
  }

  function randomAiGuessDelayMs(remainingMs) {
    var maxDelay = Math.min(AI_GUESS_MAX_DELAY_MS, Math.max(0, remainingMs - 1000));
    if (maxDelay < AI_GUESS_MIN_DELAY_MS) return 0;
    return AI_GUESS_MIN_DELAY_MS + Math.floor(Math.random() * (maxDelay - AI_GUESS_MIN_DELAY_MS + 1));
  }

  function captureUserCanvasPng() {
    if (!els.canvas) return '';
    try {
      var exportCanvas = document.createElement('canvas');
      exportCanvas.width = els.canvas.width;
      exportCanvas.height = els.canvas.height;
      var exportContext = exportCanvas.getContext('2d');
      exportContext.fillStyle = '#fffdfa';
      exportContext.fillRect(0, 0, exportCanvas.width, exportCanvas.height);
      exportContext.drawImage(els.canvas, 0, 0);
      return exportCanvas.toDataURL('image/png');
    } catch (_) {
      try {
        return els.canvas.toDataURL('image/png');
      } catch (_) {
        return '';
      }
    }
  }

  function persistCurrentUserCanvasSnapshot() {
    if (!state.hasDrawn) return;
    state.userPng = captureUserCanvasPng() || state.userPng;
  }

  function scheduleNextRandomAiGuess() {
    clearTimeout(state.aiGuessTimer);
    state.aiGuessTimer = null;
    state.aiGuessNextAt = 0;
    if (state.phase !== 'ai_guess_feedback') return;
    if (state.aiGuessAttempts >= state.maxAiGuessAttempts) return;
    var delay = randomAiGuessDelayMs(aiGuessTimeRemainingMs());
    if (!delay) return;
    state.aiGuessNextAt = Date.now() + delay;
    state.aiGuessTimer = setTimeout(function () {
      state.aiGuessTimer = null;
      state.aiGuessNextAt = 0;
      triggerRandomAiGuess();
    }, delay);
  }

  function triggerRandomAiGuess(imageDataUrl) {
    if (state.phase !== 'ai_guess_feedback' || state.aiGuessTimeoutSettling) return;
    var fullSnapshot = captureUserCanvasPng();
    if (fullSnapshot) state.userPng = fullSnapshot;
    var commandImage = boundedVisionCommandImage(imageDataUrl) || captureVisionCommandImage();
    if (!commandImage) {
      addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
      scheduleNextRandomAiGuess();
      return;
    }
    if (state.chatInFlight || state.aiGuessInFlight) {
      state.pendingAutoGuess = true;
      state.pendingAutoGuessImage = commandImage;
      return;
    }
    state.pendingAutoGuess = false;
    state.pendingAutoGuessImage = '';
    startThinkingEventMessage('drawingGuess.messages.aiGuessing', 'She is looking at your drawing...');
    postVisionGuess('', { auto: true, image_data_url: commandImage });
  }

  function handleAiGuessTimeout() {
    stopAiGuessSchedule();
    if (state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback') return;
    state.aiGuessTimeoutSettling = true;
    updateControls();
    if (state.chatInFlight || state.aiGuessInFlight) {
      state.pendingAiGuessTimeout = true;
      return;
    }
    settleAiGuessTimeout();
  }

  function isAiGuessTimeoutSettlementActive(flowToken) {
    return isCurrentRoundFlow(flowToken)
      && state.aiGuessTimeoutSettling
      && (state.phase === 'ai_guessing' || state.phase === 'ai_guess_feedback');
  }

  function failAiGuessTimeoutSettlement(reason) {
    clearTimeout(state.aiGuessTimeoutRetryTimer);
    state.aiGuessTimeoutRetryTimer = null;
    state.pendingAiGuessTimeout = false;
    state.aiGuessTimeoutSettling = false;
    addMessage('drawingGuess.messages.roundFailed', 'Round failed: {{reason}}', {
      reason: String(reason || 'timeout_failed')
    });
    updateControls();
  }

  function settleAiGuessTimeout(attempt, phaseAdvanceAttempt, busyAttempt, busyStartedAt) {
    if (state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback') return;
    stopAiGuessSchedule();
    state.aiGuessTimeoutSettling = true;
    updateControls();
    attempt = Number(attempt || 0);
    phaseAdvanceAttempt = Number(phaseAdvanceAttempt || 0);
    busyAttempt = Number(busyAttempt || 0);
    busyStartedAt = Number(busyStartedAt || 0);
    var flowToken = state.roundFlowToken;
    return executeRoundCommand(
      ROUND_COMMANDS.TIMEOUT,
      roundCommandPayload({ timeout_kind: 'ai_guessing' }),
      AI_GUESS_SETTLEMENT_REQUEST_TIMEOUT_MS
    ).then(function (res) {
      if (!isAiGuessTimeoutSettlementActive(flowToken)) return;
      var responsePhase = String((res && (res.phase || (res.state && res.state.phase))) || '');
      if (responsePhase === 'summary') {
        state.pendingAiGuessTimeout = false;
        if (res && res.message) {
          addNekoMessage(res.message);
          if (res.kind === 'ai_guess' && res.guess) {
            addEventMessage('drawingGuess.messages.aiGuessLine', 'She guessed: {{guess}}', {
              guess: res.guess.label || ''
            });
            addAiGuessOutcomeMessage(res);
          }
        }
        renderSummary(res || {});
        return;
      }
      if (!res || !res.ok) {
        if (res && res.reason === 'session_busy') {
          var busyWindowStartedAt = busyStartedAt || Date.now();
          var busyElapsedMs = Math.max(0, Date.now() - busyWindowStartedAt);
          if (busyAttempt >= AI_GUESS_TIMEOUT_BUSY_MAX_POLLS
            || busyElapsedMs >= AI_GUESS_TIMEOUT_BUSY_RETRY_WINDOW_MS) {
            failAiGuessTimeoutSettlement('session_busy');
            return;
          }
          state.pendingAiGuessTimeout = true;
          clearTimeout(state.aiGuessTimeoutRetryTimer);
          var retryWhenReady = function () {
            state.aiGuessTimeoutRetryTimer = null;
            if (!isAiGuessTimeoutSettlementActive(flowToken)) return;
            if (Date.now() - busyWindowStartedAt >= AI_GUESS_TIMEOUT_BUSY_RETRY_WINDOW_MS) {
              failAiGuessTimeoutSettlement('session_busy');
              return;
            }
            if (state.chatInFlight || state.aiGuessInFlight) {
              state.aiGuessTimeoutRetryTimer = setTimeout(retryWhenReady, 120);
              return;
            }
            state.pendingAiGuessTimeout = false;
            settleAiGuessTimeout(attempt, phaseAdvanceAttempt, busyAttempt + 1, busyWindowStartedAt);
          };
          state.aiGuessTimeoutRetryTimer = setTimeout(
            retryWhenReady,
            Math.min(
              AI_GUESS_TIMEOUT_BUSY_RETRY_DELAY_MS,
              Math.max(1, AI_GUESS_TIMEOUT_BUSY_RETRY_WINDOW_MS - busyElapsedMs)
            )
          );
          return;
        }
        failAiGuessTimeoutSettlement((res && res.reason) || 'timeout_failed');
        return;
      }
      state.pendingAiGuessTimeout = false;
      if (res.message) addNekoMessage(res.message);
      if (responsePhase === 'ai_guessing' || responsePhase === 'ai_guess_feedback') {
        setPhase(responsePhase);
        if (phaseAdvanceAttempt < AI_GUESS_TIMEOUT_PHASE_ADVANCE_MAX_RETRIES) {
          return settleAiGuessTimeout(0, phaseAdvanceAttempt + 1, 0);
        }
        failAiGuessTimeoutSettlement('timeout_not_settled');
        return;
      }
      failAiGuessTimeoutSettlement('timeout_not_settled');
    }).catch(function (err) {
      if (!isAiGuessTimeoutSettlementActive(flowToken)) return;
      clearTimeout(state.aiGuessTimeoutRetryTimer);
      if (attempt < AI_GUESS_TIMEOUT_MAX_RETRIES) {
        state.aiGuessTimeoutRetryTimer = setTimeout(function () {
          if (!isAiGuessTimeoutSettlementActive(flowToken)) return;
          settleAiGuessTimeout(attempt + 1, phaseAdvanceAttempt, busyAttempt, busyStartedAt);
        }, AI_GUESS_TIMEOUT_RETRY_BASE_DELAY_MS * Math.pow(2, attempt));
        return;
      }
      failAiGuessTimeoutSettlement(readableRequestError(err));
    }).finally(updateControls);
  }

  function flushDeferredAiGuessWork() {
    if (state.aiGuessInFlight || state.chatInFlight) return;
    if (state.pendingAiGuessTimeout) {
      state.pendingAiGuessTimeout = false;
      settleAiGuessTimeout();
      return;
    }
    if (state.pendingSupplementGuess) {
      var supplementImage = state.pendingSupplementImage;
      state.pendingSupplementGuess = false;
      state.pendingSupplementImage = '';
      triggerSupplementGuess(false, supplementImage);
      return;
    }
    if (state.pendingAutoGuess) {
      var autoImage = state.pendingAutoGuessImage;
      state.pendingAutoGuessImage = '';
      triggerRandomAiGuess(autoImage);
    }
  }

  function startGame() {
    readMemoryConsent();
    els.tutorialOverlay.hidden = true;
    updateControls();
    startRoute().then(function (ok) {
      if (ok) {
        startRound();
        return;
      }
      // 启动失败时教程层是唯一的开始入口，必须还回来，否则只能刷新页面
      els.tutorialOverlay.hidden = false;
      updateControls();
    });
  }

  function resetRoundStartState() {
    var token = beginRoundFlow();
    state.roundSessionReady = false;
    clearNekoVoiceQueue();
    // 后端 _require_session 校验的是 round/start 当时存的 token；此后前端
    // 因终态作废等原因 bump roundFlowToken 时（如 renderFinalSummary），发给
    // 后端的 client_round_token 必须继续用本轮实际注册的值，否则 final_summary
    // 里的聊天 /input 会被判 stale_round_flow
    state.activeRoundToken = token;
    hideExitReopenButton();
    hideExitConfirm(false);
    stopCountdown();
    resetCanvas();
    // 上一轮画布可能已发布到 route state，立即推 clear（内部只在有过发布时
    // 才发请求），避免下一次视觉请求拿到上一轮的旧画布
    pushCanvasContextForRoute(true);
    state.roundNumber += 1;
    state.currentRoundSummarySaved = false;
    state.aiSvg = '';
    state.aiDrawingPlan = null;
    state.aiAnswerLabel = '';
    state.userPng = '';
    state.userDrawAnswer = null;
    state.drawPickOptions = [];
    state.drawPickSeconds = ROUND_FALLBACK_SECONDS;
    state.drawPickChoosing = false;
    state.aiGuessAttempts = 0;
    state.maxAiGuessAttempts = 3;
    state.aiGuessInFlight = false;
    state.chatInFlight = false;
    state.pendingSupplementGuess = false;
    state.pendingSupplementImage = '';
    state.pendingAutoGuess = false;
    state.pendingAutoGuessImage = '';
    stopThinkingEventMessage();
    stopDrawPickAnimation();
    stopAiGuessSchedule();
    setPhase('loading_round');
    showPlaceholder();
    setBadge(t('drawingGuess.phases.loading_round', 'Loading'));
    return token;
  }

  function startRound() {
    var flowToken = resetRoundStartState();
    return executeRoundCommand(ROUND_COMMANDS.START, roundCommandPayload(), 10000)
      .then(function (res) {
        ensureCurrentRoundFlow(flowToken);
        if (!res || !res.ok) throw new Error((res && res.reason) || 'round_start_failed');
        state.roundSessionReady = true;
        updateControls();
        setPhase('ai_drawing');
        setBadge(t('drawingGuess.phases.ai_drawing', 'Neko drawing'));
        scheduleAiDrawingPlaceholderHint();
        return executeRoundCommand(ROUND_COMMANDS.AI_DRAW, roundCommandPayload(), AI_DRAW_REQUEST_TIMEOUT_MS);
      })
      .then(function (res) {
        ensureCurrentRoundFlow(flowToken);
        // final_summary/ended 等终态优先于迟到的绘图结果。token 是异步代次护栏，
        // phase 再兜住任何未经过 beginRoundFlow 的终态入口，避免结算页被拉回猜测流程。
        if (state.phase !== 'ai_drawing') return;
        if (!res || !res.ok) throw new Error((res && res.reason) || 'ai_draw_failed');
        if (res.skipped && res.reason === 'not_ai_drawing') {
          return;
        }
        var responseDrawing = res.drawing && typeof res.drawing === 'object' ? res.drawing : {};
        var draftPlan = aiDrawingPlanFromResponse(res);
        var draftVisible = false;
        if (draftPlan) {
          state.aiDrawingPlan = draftPlan;
          state.aiSvg = aiDrawingPlanToSvg(draftPlan) || String(responseDrawing.svg || '');
          draftVisible = showAiDrawingPlan(draftPlan);
        } else {
          state.aiDrawingPlan = null;
          state.aiSvg = String(responseDrawing.svg || '');
        }
        if (!draftVisible) {
          showAiDrawing(state.aiSvg);
        }
        setPhase('user_guessing');
        setChatPlaceholder('drawingGuess.input.guessPlaceholder', 'Type your guess or ask for a hint');
        addNekoMessage(res.message || t('drawingGuess.messages.aiDrawingReady', 'She finished drawing. Try to guess it.'));
        startCountdown(res.guess_seconds || ROUND_FALLBACK_SECONDS, handleGuessTimeout);
        reviewAiDrawingInBackground(res.drawing, flowToken);
      })
      .catch(function (err) {
        if (err && err.staleRoundFlow) return;
        setPhase('tutorial');
        showPlaceholder();
        // 教程层是唯一的开始入口；回退到 tutorial 相位时必须一并还原，
        // 否则 round 启动失败后没有任何可见的重试控件
        if (els.tutorialOverlay) els.tutorialOverlay.hidden = false;
        addMessage('drawingGuess.messages.roundFailed', 'Round failed: {{reason}}', { reason: readableRequestError(err) });
      })
      .finally(updateControls);
  }

  function continueAfterAiDrawingHalf(res, flowToken) {
    if (!isCurrentRoundFlow(flowToken)) return;
    cancelGuessTimeoutRetry();
    prepareUserDrawing(res.user_draw_options || res.user_draw_answer, res.draw_seconds || ROUND_FALLBACK_SECONDS);
  }

  function isGuessTimeoutFlowActive(flowToken) {
    return isCurrentRoundFlow(flowToken) && state.phase === 'loading_round';
  }

  function isGuessTimeoutResult(res) {
    var hasOptions = Array.isArray(res && res.user_draw_options)
      && res.user_draw_options.some(function (option) { return option && option.id; });
    var hasAnswer = !!(res && res.user_draw_answer && res.user_draw_answer.id);
    return !!res
      && !!res.ok
      && !!(res.answer && res.answer.id)
      && (hasOptions || hasAnswer);
  }

  function requestGuessTimeout(flowToken, attempt) {
    if (!isGuessTimeoutFlowActive(flowToken)) return Promise.resolve();
    return executeRoundCommand(
      ROUND_COMMANDS.TIMEOUT,
      roundCommandPayload({ timeout_kind: 'user_guessing' }),
      10000
    ).then(function (res) {
      if (!isGuessTimeoutFlowActive(flowToken)) return;
      if (!res || !res.ok) throw new Error((res && res.reason) || 'timeout_failed');
      if (!isGuessTimeoutResult(res)) throw new Error('timeout_transition_unavailable');
      state.guessTimeoutRetryTimer = null;
      addNekoMessage(res.message || t('drawingGuess.messages.guessTimeout', 'Time is up. The answer was {{answer}}.', {
        answer: res.answer ? res.answer.label : ''
      }));
      state.aiAnswerLabel = res.answer ? String(res.answer.label || '') : '';
      addEventMessage('drawingGuess.messages.answerReveal', 'Answer: {{answer}}', { answer: res.answer ? res.answer.label : '' });
      continueAfterAiDrawingHalf(res, flowToken);
    }).catch(function (err) {
      if (!isGuessTimeoutFlowActive(flowToken)) return;
      if (attempt === 0) {
        addMessage('drawingGuess.messages.roundFailed', 'Round failed: {{reason}}', {
          reason: readableRequestError(err)
        });
      }
      clearTimeout(state.guessTimeoutRetryTimer);
      state.guessTimeoutRetryTimer = setTimeout(function () {
        if (!isGuessTimeoutFlowActive(flowToken)) return;
        requestGuessTimeout(flowToken, attempt + 1);
      }, Math.min(GUESS_TIMEOUT_RETRY_MAX_DELAY_MS, GUESS_TIMEOUT_RETRY_BASE_DELAY_MS * Math.pow(2, attempt)));
    });
  }

  function handleGuessTimeout() {
    var flowToken = state.roundFlowToken;
    setPhase('loading_round');
    requestGuessTimeout(flowToken, 0);
  }

  function submitUserGuess(text, inputMetadata) {
    var flowToken = state.roundFlowToken;
    return executeRoundCommand(ROUND_COMMANDS.INPUT, roundCommandPayload(Object.assign({
      text: text
    }, inputMetadata || {})), ROUND_INPUT_REQUEST_TIMEOUT_MS).then(function (res) {
      if (!isCurrentRoundFlow(flowToken)) return;
      if (state.phase !== 'user_guessing' && state.phase !== 'loading_round') return;
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      addNekoMessage(res.message || '');
      if (res.correct || res.kind === 'give_up') {
        stopCountdown();
        cancelGuessTimeoutRetry();
        state.aiAnswerLabel = res.answer ? String(res.answer.label || '') : '';
        addEventMessage('drawingGuess.messages.answerReveal', 'Answer: {{answer}}', {
          answer: res.answer ? res.answer.label : ''
        });
        continueAfterAiDrawingHalf(res, flowToken);
      }
    }).finally(function () {
      if (!isCurrentRoundFlow(flowToken)) return;
      stopThinkingEventMessage();
      updateControls();
    });
  }

  function submitGameChat(text, options) {
    options = options || {};
    var flowToken = state.roundFlowToken;
    state.chatInFlight = true;
    return executeRoundCommand(ROUND_COMMANDS.INPUT, roundCommandPayload(Object.assign({
      text: text,
      summary_chat_only: !!options.summaryChatOnly
    }, options.inputMetadata || {})), ROUND_INPUT_REQUEST_TIMEOUT_MS).then(function (res) {
      if (!isCurrentRoundFlow(flowToken)) return;
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      addNekoMessage(res.message);
    }).finally(function () {
      if (!isCurrentRoundFlow(flowToken)) return;
      state.chatInFlight = false;
      flushDeferredAiGuessWork();
      updateControls();
    });
  }

  function submitFeedbackInput(text, inputMetadata) {
    var flowToken = state.roundFlowToken;
    var feedbackImage = captureUserCanvasPng();
    if (feedbackImage) state.userPng = feedbackImage;
    var feedbackCommandImage = captureVisionCommandImage();
    if (!feedbackCommandImage) {
      addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
      updateControls();
      return Promise.resolve();
    }
    state.chatInFlight = true;
    return executeRoundCommand(ROUND_COMMANDS.FEEDBACK, roundCommandPayload(Object.assign({
      text: text,
      image_data_url: feedbackCommandImage
    }, inputMetadata || {})), AI_GUESS_REQUEST_TIMEOUT_MS).then(function (res) {
      if (!isCurrentRoundFlow(flowToken)) return;
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      if (res.kind === 'ai_guess') {
        cancelAiGuessBusyRetry();
        var guessLabel = res.guess ? res.guess.label : '';
        state.pendingAutoGuess = false;
        state.aiGuessAttempts = Number(res.attempt || state.aiGuessAttempts || 0);
        state.maxAiGuessAttempts = Number(res.max_attempts || state.maxAiGuessAttempts || 3);
        addNekoMessage(res.message);
        addEventMessage('drawingGuess.messages.aiGuessLine', 'She guessed: {{guess}}', { guess: guessLabel });
        addAiGuessOutcomeMessage(res);
        if (res.state && res.state.phase === 'summary') {
          renderSummary(res);
        } else {
          var nextPhase = res.state && res.state.phase ? String(res.state.phase) : 'ai_guess_feedback';
          setPhase(nextPhase);
          if (nextPhase === 'ai_guess_feedback') {
            setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Keep chatting; give a hint when you want her to guess again');
            addEventMessage('drawingGuess.messages.aiNeedsHint', 'You can just keep chatting. Give her a hint when you want another guess.');
            scheduleNextRandomAiGuess();
          }
        }
        return;
      }
      addNekoMessage(res.message);
    }).finally(function () {
      if (!isCurrentRoundFlow(flowToken)) return;
      state.chatInFlight = false;
      flushDeferredAiGuessWork();
      updateControls();
    });
  }

  function beginUserDrawing(answer, seconds) {
    stopDrawPickAnimation();
    state.userDrawAnswer = answer || null;
    resetCanvas();
    showCanvas();
    setPhase('user_drawing');
    setChatPlaceholder('drawingGuess.input.drawingPlaceholder', 'Chat while drawing');
    addEventMessage('drawingGuess.messages.userDrawPrompt', 'Your word is: {{answer}}. Draw it for her.', {
      answer: answer ? answer.label : ''
    });
    startCountdown(seconds || ROUND_FALLBACK_SECONDS, function () {
      submitDrawing(false);
    });
  }

  function chooseUserDrawWord(wordId) {
    if (state.phase !== 'drawing_pick' || state.drawPickChoosing) return;
    if (!els.drawPick.classList.contains('dg-draw-pick-ready')) return;
    var selected = state.drawPickOptions.find(function (option) {
      return option && option.id === wordId;
    });
    if (!selected) return;

    state.drawPickChoosing = true;
    Array.prototype.slice.call(els.drawPick.querySelectorAll('.dg-pick-option')).forEach(function (button) {
      var isSelected = button.getAttribute('data-word-id') === wordId;
      button.disabled = true;
      button.classList.toggle('is-selected', isSelected);
    });
    els.drawPick.querySelector('.dg-draw-pick-reveal').textContent = t('drawingGuess.messages.drawingPickReveal', 'Chosen: {{answer}}', {
      answer: selected.label || selected.id
    });
    updateControls();

    var restoreDrawPick = function () {
      state.drawPickChoosing = false;
      renderDrawPickOptions(state.drawPickOptions);
      els.drawPick.classList.remove('dg-draw-pick-spread');
      els.drawPick.classList.add('dg-draw-pick-ready', 'dg-draw-pick-revealed');
      addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
    };
    var flowToken = state.roundFlowToken;
    return executeRoundCommand(ROUND_COMMANDS.CHOOSE_WORD, roundCommandPayload({ word_id: wordId }), 10000).then(function (res) {
      // 与其它异步回合回调一致：End/换轮之后的迟到 choose-word 结果不再落地
      if (!isCurrentRoundFlow(flowToken)) return;
      if (!res || !res.ok) {
        // The first request may have reached the backend even when its response
        // was lost. A retry then reports the canonical user_drawing state;
        // resume from that state instead of trapping the player in the picker.
        var recoveredState = res && res.reason === 'not_word_picking'
          && res.state && res.state.phase === 'user_drawing'
          ? res.state
          : null;
        var recoveredAnswer = recoveredState && recoveredState.user_draw_answer;
        if (recoveredAnswer && recoveredAnswer.id) {
          beginUserDrawing(
            recoveredAnswer,
            (recoveredState.timers && recoveredState.timers.draw_seconds)
              || state.drawPickSeconds
              || ROUND_FALLBACK_SECONDS
          );
          return;
        }
        restoreDrawPick();
        return;
      }
      beginUserDrawing(res.user_draw_answer || selected, res.draw_seconds || state.drawPickSeconds || ROUND_FALLBACK_SECONDS);
    }).catch(function () {
      // 超时/网络失败与 !res.ok 同样要把选词卡还给玩家，否则选词界面永久锁死
      if (!isCurrentRoundFlow(flowToken)) return;
      restoreDrawPick();
    }).finally(updateControls);
  }

  function prepareUserDrawing(options, seconds) {
    state.userDrawAnswer = null;
    var drawOptions = (Array.isArray(options) ? options : (options ? [options] : [])).filter(function (option) {
      return option && option.id;
    });
    if (!drawOptions.length) {
      addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
      return;
    }
    showDrawPickAnimation(drawOptions, seconds);
    addEventMessage('drawingGuess.messages.drawingPickTitle', 'Drawing a word');
  }

  function submitDrawing(manual) {
    if (!isCanvasEditablePhase() || state.aiGuessTimeoutSettling) return;
    if (manual && !state.hasDrawn) {
      addNekoMessage(t('drawingGuess.messages.blankCanvas', 'Give her a few lines first.'));
      return;
    }
    if (state.phase !== 'user_drawing') {
      triggerSupplementGuess(true);
      return;
    }
    state.userPng = captureUserCanvasPng();
    var commandImage = captureVisionCommandImage();
    if (!commandImage) {
      addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
      if (!manual) {
        setPhase('ai_guessing');
        settleAiGuessTimeout();
      }
      return;
    }
    stopCountdown();
    stopAiGuessSchedule();
    state.aiGuessDeadline = Date.now() + ROUND_FALLBACK_SECONDS * 1000;
    state.aiGuessAttempts = 0;
    setPhase('ai_guessing');
    setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Keep chatting; give a hint when you want her to guess again');
    startThinkingEventMessage('drawingGuess.messages.aiGuessing', 'She is looking at your drawing...');
    startCountdown(ROUND_FALLBACK_SECONDS, handleAiGuessTimeout);
    postVisionGuess('', { first_guess: true, image_data_url: commandImage });
  }

  function triggerSupplementGuess(announce, imageDataUrl) {
    if ((state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback')
      || state.aiGuessTimeoutSettling) return;
    var fullSnapshot = captureUserCanvasPng();
    if (fullSnapshot) state.userPng = fullSnapshot;
    var commandImage = boundedVisionCommandImage(imageDataUrl) || captureVisionCommandImage();
    if (!commandImage) {
      addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
      return;
    }
    clearTimeout(state.aiGuessTimer);
    state.aiGuessTimer = null;
    state.pendingAutoGuess = false;
    state.pendingAutoGuessImage = '';
    if (state.aiGuessInFlight || state.chatInFlight) {
      state.pendingSupplementGuess = true;
      state.pendingSupplementImage = commandImage;
      return;
    }
    state.pendingSupplementGuess = false;
    state.pendingSupplementImage = '';
    startThinkingEventMessage('drawingGuess.messages.aiGuessing', 'She is looking at your drawing...');
    postVisionGuess('', { supplement: true, image_data_url: commandImage });
  }

  function postVisionGuess(userHint, options) {
    if (state.aiGuessTimeoutSettling) return Promise.resolve();
    var flowToken = state.roundFlowToken;
    var imageDataUrl = boundedVisionCommandImage(options && options.image_data_url)
      || captureVisionCommandImage();
    if (!imageDataUrl) {
      addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
      stopThinkingEventMessage();
      updateControls();
      return Promise.resolve();
    }
    // A valid newer guess supersedes any retry that was waiting on an earlier
    // session_busy response in this same round.
    cancelAiGuessBusyRetry();
    var busyRetryGeneration = state.aiGuessBusyRetryGeneration;
    state.aiGuessInFlight = true;
    return executeRoundCommand(ROUND_COMMANDS.VISION_GUESS, roundCommandPayload({
      image_data_url: imageDataUrl,
      user_hint: userHint || '',
      settle_on_miss: !!(options && options.settle_on_miss),
      time_expired: !!(options && options.settle_on_miss)
    }), AI_GUESS_REQUEST_TIMEOUT_MS).then(function (res) {
      if (!isCurrentRoundFlow(flowToken)) return;
      stopThinkingEventMessage();
      if (!res || !res.ok) {
        if (res && res.reason === 'session_busy' && Number((options && options.busy_retry_count) || 0) < 3) {
          if (state.aiGuessBusyRetryGeneration !== busyRetryGeneration) return;
          var retryOptions = Object.assign({}, options || {}, {
            busy_retry_count: Number((options && options.busy_retry_count) || 0) + 1
          });
          var retryWhenReady = function () {
            if (state.aiGuessBusyRetryGeneration !== busyRetryGeneration) return;
            state.aiGuessBusyRetryTimer = null;
            if (!isCurrentRoundFlow(flowToken)) return;
            if (state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback') return;
            if (state.aiGuessInFlight || state.chatInFlight) {
              state.aiGuessBusyRetryTimer = setTimeout(retryWhenReady, 120);
              return;
            }
            postVisionGuess(userHint, retryOptions);
          };
          state.aiGuessBusyRetryTimer = setTimeout(retryWhenReady, 180);
          return;
        }
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      var guessLabel = res.guess ? res.guess.label : '';
      state.pendingAutoGuess = false;
      state.aiGuessAttempts = Number(res.attempt || state.aiGuessAttempts || 0);
      state.maxAiGuessAttempts = Number(res.max_attempts || state.maxAiGuessAttempts || 3);
      addNekoMessage(res.message);
      addEventMessage('drawingGuess.messages.aiGuessLine', 'She guessed: {{guess}}', { guess: guessLabel });
      addAiGuessOutcomeMessage(res);
      if (res.state && res.state.phase === 'summary') {
        renderSummary(res);
      } else {
        setPhase('ai_guess_feedback');
        setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Keep chatting; give a hint when you want her to guess again');
        addEventMessage('drawingGuess.messages.aiNeedsHint', 'You can just keep chatting. Give her a hint when you want another guess.');
        scheduleNextRandomAiGuess();
      }
    }).catch(function () {
      if (!isCurrentRoundFlow(flowToken)) return;
      addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
    }).finally(function () {
      if (!isCurrentRoundFlow(flowToken)) return;
      state.aiGuessInFlight = false;
      stopThinkingEventMessage();
      flushDeferredAiGuessWork();
      updateControls();
    });
  }

  function renderSummary(res) {
    state.aiGuessTimeoutSettling = false;
    stopCountdown();
    stopThinkingEventMessage();
    stopDrawPickAnimation();
    stopAiGuessSchedule();
    persistCurrentUserCanvasSnapshot();
    setPhase('summary');
    showSummary();
    var answerLabel = res.answer
      ? res.answer.label
      : (res.state && res.state.user_draw_answer
        ? res.state.user_draw_answer.label
        : (state.userDrawAnswer ? state.userDrawAnswer.label : ''));
    var evaluation = String(res.evaluation || res.message || '').trim();
    var summary = upsertCurrentRoundSummary({
      round: state.roundNumber || Math.max(1, state.roundSummaries.length + 1),
      aiAnswerLabel: state.aiAnswerLabel || '',
      answerLabel: answerLabel,
      evaluation: evaluation,
      aiSvg: state.aiSvg || '',
      userPng: state.userPng || ''
    });
    renderSummaryList([summary], false);
    setChatPlaceholder('drawingGuess.input.summaryPlaceholder', 'Chat about this round, or start another one');
    updateControls();
  }

  function upsertCurrentRoundSummary(summary) {
    var round = Number(summary.round || 0);
    var existingIndex = state.roundSummaries.findIndex(function (item) {
      return Number(item.round || 0) === round;
    });
    if (existingIndex >= 0) {
      state.roundSummaries[existingIndex] = Object.assign({}, state.roundSummaries[existingIndex], summary);
      state.currentRoundSummarySaved = true;
      return state.roundSummaries[existingIndex];
    }
    state.roundSummaries.push(summary);
    state.currentRoundSummarySaved = true;
    return summary;
  }

  function renderFinalSummary() {
    // 最终结算是回合终态：无论从结束按钮还是 route end 进入，都统一作废仍在途的
    // round-start / ai-draw / vision-guess / timeout 回调。
    beginRoundFlow();
    clearNekoVoiceQueue();
    state.aiGuessInFlight = false;
    state.chatInFlight = false;
    state.pendingAutoGuess = false;
    state.pendingAutoGuessImage = '';
    state.pendingSupplementGuess = false;
    state.pendingSupplementImage = '';
    stopCountdown();
    stopThinkingEventMessage();
    stopDrawPickAnimation();
    stopAiGuessSchedule();
    showSummary();
    setPhase('final_summary');
    publishFinalSummaryRouteState();
    renderSummaryList(state.roundSummaries, true);
    setChatPlaceholder('drawingGuess.input.summaryPlaceholder', 'Chat about this round, or start another one');
  }

  function renderSummaryList(summaries, finalSummary) {
    var list = Array.isArray(summaries) ? summaries : [];
    els.summary.classList.toggle('dg-summary-final', !!finalSummary);
    var title = finalSummary
      ? t('drawingGuess.summary.finalTitle', 'Final summary')
      : t('drawingGuess.summary.title', 'Round summary');
    var intro = finalSummary && !list.length
      ? '<p class="dg-summary-evaluation">' + escapeHtml(t('drawingGuess.summary.noRounds', 'No completed rounds yet.')) + '</p>'
      : '';
    els.summary.innerHTML = ''
      + '<div>'
      + '<h3>' + escapeHtml(title) + '</h3>'
      + intro
      + '</div>'
      + '<div class="dg-summary-list">'
      + list.map(function (summary, index) {
        return renderSummaryCard(summary, index, finalSummary);
      }).join('')
      + '</div>';
    list.forEach(function (summary, index) {
      var aiArt = els.summary.querySelector('[data-summary-ai-art="' + index + '"]');
      if (aiArt) {
        aiArt.innerHTML = summary.aiSvg || '';
        normalizeAiDrawingSvg(aiArt.querySelector('svg'));
      }
    });
    Array.prototype.slice.call(els.summary.querySelectorAll('[data-save-ai-svg-index]')).forEach(function (button) {
      button.addEventListener('click', function () {
        var summary = list[Number(button.getAttribute('data-save-ai-svg-index') || 0)];
        if (summary) saveAiSvgFile(summary.aiSvg || '', 'neko-drawing-round-' + summary.round + '.svg');
      });
    });
    Array.prototype.slice.call(els.summary.querySelectorAll('[data-save-ai-png-index]')).forEach(function (button) {
      button.addEventListener('click', function () {
        var summary = list[Number(button.getAttribute('data-save-ai-png-index') || 0)];
        if (summary) saveAiPngFile(summary.aiSvg || '', 'neko-drawing-round-' + summary.round + '.png');
      });
    });
    Array.prototype.slice.call(els.summary.querySelectorAll('[data-save-user-png-index]')).forEach(function (button) {
      button.addEventListener('click', function () {
        var summary = list[Number(button.getAttribute('data-save-user-png-index') || 0)];
        if (summary && summary.userPng) saveUserPngFile(summary.userPng, 'your-drawing-round-' + summary.round + '.png');
      });
    });
  }

  function renderSummaryCard(summary, index, finalSummary) {
    var roundLabel = finalSummary
      ? '<h4>' + escapeHtml(t('drawingGuess.summary.roundLabel', 'Round {{round}}', { round: summary.round })) + '</h4>'
      : '';
    var nekoTitle = summaryArtworkTitle('drawingGuess.summary.nekoArt', 'Neko drawing', summary.aiAnswerLabel || '');
    var userTitle = summaryArtworkTitle('drawingGuess.summary.userArt', 'Your drawing', summary.answerLabel || '');
    return '<section class="dg-round-summary">'
      + roundLabel
      + (summary.evaluation ? '<p class="dg-summary-evaluation">' + escapeHtml(summary.evaluation) + '</p>' : '')
      + '<div class="dg-summary-grid">'
      + '<section class="dg-thumb"><h4>' + escapeHtml(nekoTitle) + '</h4><div class="dg-thumb-preview" data-summary-ai-art="' + index + '"></div><div class="dg-thumb-actions"><button class="dg-button" type="button" data-save-ai-svg-index="' + index + '">' + escapeHtml(t('drawingGuess.actions.saveNekoSvg', 'Save as SVG')) + '</button><button class="dg-button" type="button" data-save-ai-png-index="' + index + '">' + escapeHtml(t('drawingGuess.actions.saveNekoPng', 'Save as PNG')) + '</button></div></section>'
      + '<section class="dg-thumb"><h4>' + escapeHtml(userTitle) + '</h4><div class="dg-thumb-preview">' + (summary.userPng ? '<img alt="" src="' + escapeAttr(summary.userPng) + '">' : '') + '</div><div class="dg-thumb-actions"><button class="dg-button" type="button" data-save-user-png-index="' + index + '">' + escapeHtml(t('drawingGuess.actions.saveUserPng', 'Save my drawing')) + '</button></div></section>'
      + '</div>'
      + '</section>';
  }

  function summaryArtworkTitle(key, fallback, answerLabel) {
    var title = t(key, fallback);
    var answer = String(answerLabel || '').trim();
    if (!answer) return title;
    var language = currentLanguage().toLowerCase();
    var separator = (language.indexOf('zh') === 0 || language.indexOf('ja') === 0 || language.indexOf('ko') === 0) ? '：' : ': ';
    return title + separator + answer;
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, '&#96;');
  }

  function downloadBlob(filename, content, type) {
    var blob = content instanceof Blob ? content : new Blob([content], { type: type || 'application/octet-stream' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  function saveAiSvg() {
    saveAiSvgFile(state.aiSvg || '', 'neko-drawing.svg');
  }

  function saveAiSvgFile(svgMarkup, filename) {
    if (!svgMarkup) return;
    downloadBlob(filename, svgMarkup, 'image/svg+xml;charset=utf-8');
  }

  function saveAiPngFile(svgMarkup, filename) {
    svgMarkupToPngBlob(svgMarkup).then(function (blob) {
      if (blob) downloadBlob(filename, blob, 'image/png');
    }).catch(function () {});
  }

  function svgMarkupToPngBlob(svgMarkup) {
    return new Promise(function (resolve, reject) {
      if (!svgMarkup) {
        resolve(null);
        return;
      }
      var doc = new DOMParser().parseFromString(svgMarkup, 'image/svg+xml');
      var svg = doc.documentElement;
      if (!svg || String(svg.tagName || '').toLowerCase() !== 'svg') {
        resolve(null);
        return;
      }
      normalizeAiDrawingSvg(svg);
      svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      svg.setAttribute('width', '800');
      svg.setAttribute('height', '600');
      var serialized = new XMLSerializer().serializeToString(svg);
      var svgBlob = new Blob([serialized], { type: 'image/svg+xml;charset=utf-8' });
      var url = URL.createObjectURL(svgBlob);
      var image = new Image();
      image.onload = function () {
        try {
          var canvas = document.createElement('canvas');
          canvas.width = 800;
          canvas.height = 600;
          var context = canvas.getContext('2d');
          context.fillStyle = '#fffdfa';
          context.fillRect(0, 0, canvas.width, canvas.height);
          context.drawImage(image, 0, 0, canvas.width, canvas.height);
          canvas.toBlob(function (blob) {
            URL.revokeObjectURL(url);
            resolve(blob);
          }, 'image/png');
        } catch (error) {
          URL.revokeObjectURL(url);
          reject(error);
        }
      };
      image.onerror = function () {
        URL.revokeObjectURL(url);
        resolve(null);
      };
      image.src = url;
    });
  }

  function saveUserPng() {
    saveUserPngFile(state.userPng, 'your-drawing.png');
  }

  function saveUserPngFile(dataUrl, filename) {
    if (!dataUrl) return;
    fetch(dataUrl).then(function (res) { return res.blob(); }).then(function (blob) {
      downloadBlob(filename, blob, 'image/png');
    }).catch(function () {});
  }

  function disposeAvatarController() {
    state.avatarLoadToken += 1;
    stopDrawingGuessLipSync();
    if (!state.avatarController) return;
    try { state.avatarController.dispose(); } catch (_) {}
    state.avatarController = null;
  }

  function showConfiguredFallback() {
    showModelLayer('fallback');
    setModelLoadState('fallback');
  }

  function mountAvatarDescriptor(client, descriptor, loadToken) {
    if (!descriptor || !descriptor.rendererAvailable || !descriptor.model) {
      showConfiguredFallback();
      return Promise.resolve(false);
    }
    var modelKind = String(descriptor.model.type || '').toLowerCase();
    var previousMount = state.avatarMountPromise;
    var mountPromise = (previousMount
      ? previousMount.catch(function () {})
      : Promise.resolve()
    ).then(function () {
      if (loadToken !== state.avatarLoadToken || descriptor.name !== state.lanlanName) return null;
      return client.avatar.mount({
        slot: 'drawing-guess-character',
        characterName: descriptor.name,
        model: descriptor.model,
        viewport: { mode: 'container' },
        fit: {
          mode: 'contain',
          align: 'center',
          padding: 0,
          scaleMultiplier: 1
        },
        resize: { mode: 'container' }
      });
    }).then(function (controller) {
      if (!controller) return false;
      if (loadToken !== state.avatarLoadToken || descriptor.name !== state.lanlanName) {
        controller.dispose();
        return false;
      }
      state.avatarController = controller;
      showModelLayer(modelKind);
      setModelLoadState('ready');
      applyModelView();
      setModelMood(state.modelMood);
      if (state.lipSyncActive) {
        Promise.resolve(controller.setSpeaking(true)).catch(function () {});
      }
      return true;
    });
    var trackedMount = mountPromise.finally(function () {
      if (state.avatarMountPromise === trackedMount) state.avatarMountPromise = null;
    });
    state.avatarMountPromise = trackedMount;
    return trackedMount;
  }

  function avatarDescriptor(name) {
    return connectMiniGameSdk().then(function (client) {
      var requestedName = String(name || '').trim();
      var request = requestedName
        ? client.avatar.getCharacter(requestedName)
        : client.avatar.getCurrentCharacter();
      return Promise.resolve(request).then(function (descriptor) {
        return { client: client, descriptor: descriptor };
      });
    });
  }

  function initModelSlotForCurrentCharacter(name, knownDescriptor) {
    if (!els.modelStage) return Promise.resolve(false);
    disposeAvatarController();
    var loadToken = state.avatarLoadToken;
    showModelLayer('loading');
    setModelLoadState('loading');
    setModelMood(modelMoodForPhase(state.phase));
    var lookup = knownDescriptor
      ? connectMiniGameSdk().then(function (client) {
        return { client: client, descriptor: knownDescriptor };
      })
      : avatarDescriptor(name);
    return lookup.then(function (resolved) {
      if (loadToken !== state.avatarLoadToken) return false;
      return mountAvatarDescriptor(resolved.client, resolved.descriptor, loadToken);
    }).catch(function (error) {
      if (loadToken === state.avatarLoadToken) {
        console.warn('[drawing_guess] SDK Avatar fallback:', sdkErrorReason(error));
        showConfiguredFallback();
      }
      return false;
    });
  }

  function loadCurrentCharacter() {
    setStatus('loadingCharacter', 'Loading character');
    var requestedName = state.lanlanName;
    return avatarDescriptor(requestedName).then(function (resolved) {
      var descriptor = resolved.descriptor;
      var name = String((descriptor && descriptor.name) || '').trim();
      state.lanlanName = name;
      if (name && !state.windowLanlanName) state.windowLanlanName = name;
      if (name) setStatus('ready', 'Ready');
      else setStatus('missingCharacter', 'Missing character');
      loadModelViewSettings();
      updateControls();
      if (!name) {
        showConfiguredFallback();
        return '';
      }
      return initModelSlotForCurrentCharacter(name, descriptor).then(function () {
        return name;
      });
    }).catch(function () {
      state.lanlanName = '';
      setStatus('missingCharacter', 'Missing character');
      showConfiguredFallback();
      updateControls();
      return '';
    });
  }
  function normalizeMemoryConsent(value) {
    return String(value || '') === 'summary' ? 'summary' : 'none';
  }

  function readMemoryConsent() {
    var selected = document.querySelector('input[name="memory-consent"]:checked');
    state.memoryConsent = normalizeMemoryConsent(selected ? selected.value : 'none');
    updateControls();
  }

  function resetCanvas() {
    var ctx = els.ctx;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, els.canvas.width, els.canvas.height);
    ctx.restore();
    state.hasDrawn = false;
    state.history = [];
    state.historyTrimmed = false;
    state.redo = [];
    pushHistory();
    updateControls();
  }

  function pushHistory() {
    try {
      state.history.push(els.ctx.getImageData(0, 0, els.canvas.width, els.canvas.height));
      if (state.history.length > 30) {
        // 截断后栈底不再是空白帧，undo 到底也不能把 hasDrawn 判回 false
        state.history.shift();
        state.historyTrimmed = true;
      }
      state.redo = [];
    } catch (_) {}
    updateControls();
  }

  function restoreImage(image) {
    if (!image) return;
    els.ctx.putImageData(image, 0, 0);
  }

  function undoCanvas() {
    if (state.history.length <= 1) return;
    state.redo.push(state.history.pop());
    restoreImage(state.history[state.history.length - 1]);
    if (state.history.length <= 1 && !state.historyTrimmed) {
      // 撤销回初始空白帧：清 hasDrawn，否则手动提交会把空白画布送去 AI 猜
      state.hasDrawn = false;
    }
    updateControls();
  }

  function redoCanvas() {
    if (!state.redo.length) return;
    var image = state.redo.pop();
    state.history.push(image);
    restoreImage(image);
    if (state.history.length > 1) {
      state.hasDrawn = true;
    }
    updateControls();
  }

  function canvasPoint(event) {
    var rect = els.canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * els.canvas.width / rect.width,
      y: (event.clientY - rect.top) * els.canvas.height / rect.height
    };
  }

  function normalizeHexColor(value) {
    var color = String(value || '#24303a').trim();
    if (color[0] !== '#') color = '#' + color;
    if (/^#[0-9a-f]{3}$/i.test(color)) {
      color = '#' + color.slice(1).split('').map(function (part) { return part + part; }).join('');
    }
    return /^#[0-9a-f]{6}$/i.test(color) ? color.toLowerCase() : '#24303a';
  }

  function currentBrushColor() {
    return normalizeHexColor(els.brushColor ? els.brushColor.value : '#24303a');
  }

  function clamp01(value) {
    return Math.max(0, Math.min(1, Number(value) || 0));
  }

  function hsvToHex(hue, saturation, value) {
    var h = ((Number(hue) || 0) % 360 + 360) % 360;
    var s = clamp01(saturation);
    var v = clamp01(value);
    var c = v * s;
    var x = c * (1 - Math.abs((h / 60) % 2 - 1));
    var m = v - c;
    var rgb;
    if (h < 60) rgb = [c, x, 0];
    else if (h < 120) rgb = [x, c, 0];
    else if (h < 180) rgb = [0, c, x];
    else if (h < 240) rgb = [0, x, c];
    else if (h < 300) rgb = [x, 0, c];
    else rgb = [c, 0, x];
    return '#' + rgb.map(function (part) {
      var hex = Math.round((part + m) * 255).toString(16);
      return hex.length === 1 ? '0' + hex : hex;
    }).join('');
  }

  function normalizeColorHistory(value) {
    return Array.isArray(value)
      ? value.map(normalizeHexColor).filter(function (color, index, colors) {
        return color && colors.indexOf(color) === index;
      }).slice(0, COLOR_HISTORY_MAX_COUNT)
      : [];
  }

  function loadColorHistory() {
    state.colorHistory = normalizeColorHistory(state.colorHistory);
  }

  function saveColorHistory() {
    state.colorHistory = normalizeColorHistory(state.colorHistory);
    queueSdkPreferenceWrite('colorHistory');
  }

  function renderColorHistory() {
    if (!els.colorHistoryColors) return;
    els.colorHistoryColors.innerHTML = '';
    state.colorHistory.slice(0, COLOR_HISTORY_VISIBLE_COUNT).forEach(function (color) {
      var button = document.createElement('button');
      button.className = 'dg-color-chip';
      button.type = 'button';
      button.dataset.color = color;
      button.title = color;
      button.style.setProperty('--chip-color', color);
      button.addEventListener('click', function () {
        setBrushColor(color, { remember: true });
      });
      button.addEventListener('contextmenu', function (event) {
        event.preventDefault();
        removeBrushColorFromHistory(color);
      });
      els.colorHistoryColors.appendChild(button);
    });
  }

  function removeBrushColorFromHistory(color) {
    var normalized = normalizeHexColor(color);
    state.colorHistory = state.colorHistory.filter(function (item) {
      return normalizeHexColor(item) !== normalized;
    }).slice(0, COLOR_HISTORY_MAX_COUNT);
    saveColorHistory();
    renderColorHistory();
  }

  function rememberBrushColor(color) {
    var normalized = normalizeHexColor(color);
    state.colorHistory = [normalized].concat(state.colorHistory.filter(function (item) {
      return normalizeHexColor(item) !== normalized;
    })).slice(0, COLOR_HISTORY_MAX_COUNT);
    saveColorHistory();
    renderColorHistory();
  }

  function setBrushColor(color, options) {
    var normalized = normalizeHexColor(color);
    if (els.brushColor) els.brushColor.value = normalized;
    [els.colorTriggerPreview, els.colorPanelPreview, els.colorPanel].forEach(function (node) {
      if (node) node.style.setProperty('--dg-current-color', normalized);
    });
    if (els.sizePreview && !els.sizePreview.hidden && els.sizePreview.dataset.previewTool === 'brush') {
      els.sizePreview.style.setProperty('--dg-size-preview-color', normalized);
    }
    if (options && options.remember) rememberBrushColor(normalized);
  }

  function setColorWheelCursor(x, y) {
    if (!els.colorWheel) return;
    els.colorWheel.style.setProperty('--dg-color-cursor-x', x + '%');
    els.colorWheel.style.setProperty('--dg-color-cursor-y', y + '%');
  }

  function colorWheelGeometry() {
    if (!els.colorWheel) return null;
    var rect = els.colorWheel.getBoundingClientRect();
    var width = Math.max(1, rect.width);
    var height = Math.max(1, rect.height);
    return {
      left: rect.left,
      top: rect.top,
      width: width,
      height: height,
      cx: rect.left + width / 2,
      cy: rect.top + height / 2,
      radius: Math.max(1, Math.min(width, height) / 2)
    };
  }

  function colorWheelHueFromAngle(angleDeg) {
    var angle = ((Number(angleDeg) || 0) % 360 + 360) % 360;
    var stops = [
      [0, 350],
      [45, 35],
      [90, 58],
      [135, 125],
      [180, 185],
      [225, 220],
      [270, 267],
      [315, 315],
      [360, 350]
    ];
    for (var i = 0; i < stops.length - 1; i += 1) {
      var from = stops[i];
      var to = stops[i + 1];
      if (angle >= from[0] && angle <= to[0]) {
        var t = (angle - from[0]) / Math.max(1, to[0] - from[0]);
        var fromHue = from[1];
        var toHue = to[1];
        var delta = toHue - fromHue;
        if (delta < -180) delta += 360;
        if (delta > 180) delta -= 360;
        return (fromHue + delta * t + 360) % 360;
      }
    }
    return angle;
  }

  function pickColorFromWheel(event, remember) {
    if (!els.colorWheel) return;
    var geometry = colorWheelGeometry();
    if (!geometry) return;
    var dx = event.clientX - geometry.cx;
    var dy = event.clientY - geometry.cy;
    var distance = Math.sqrt(dx * dx + dy * dy);
    var clampedDistance = Math.min(geometry.radius, distance);
    var angle = Math.atan2(dy, dx);
    var x = Math.cos(angle) * clampedDistance;
    var y = Math.sin(angle) * clampedDistance;
    var wheelAngle = (angle * 180 / Math.PI + 450) % 360;
    var hue = colorWheelHueFromAngle(wheelAngle);
    var saturation = clampedDistance / geometry.radius;
    setColorWheelCursor(50 + (x / geometry.radius) * 50, 50 + (y / geometry.radius) * 50);
    setBrushColor(hsvToHex(hue, saturation, 1), { remember: remember });
  }

  function hexToRgba(hex) {
    var value = normalizeHexColor(hex);
    if (value[0] === '#') value = value.slice(1);
    var parsed = /^[0-9a-f]{6}$/i.test(value) ? parseInt(value, 16) : 0x24303a;
    return {
      r: (parsed >> 16) & 255,
      g: (parsed >> 8) & 255,
      b: parsed & 255,
      a: 255
    };
  }

  function pixelMatches(data, index, color) {
    return data[index] === color.r
      && data[index + 1] === color.g
      && data[index + 2] === color.b
      && data[index + 3] === color.a;
  }

  function setPixel(data, index, color) {
    data[index] = color.r;
    data[index + 1] = color.g;
    data[index + 2] = color.b;
    data[index + 3] = color.a;
  }

  function canvasDisplayPixelBounds(canvas, displayArea) {
    var width = Math.max(1, Math.floor(Number(canvas && canvas.width) || 1));
    var height = Math.max(1, Math.floor(Number(canvas && canvas.height) || 1));
    var fullBounds = { minX: 0, minY: 0, maxX: width - 1, maxY: height - 1 };
    if (!canvas || typeof canvas.getBoundingClientRect !== 'function'
        || !displayArea || typeof displayArea.getBoundingClientRect !== 'function') {
      return fullBounds;
    }
    var canvasRect = canvas.getBoundingClientRect();
    var displayRect = displayArea.getBoundingClientRect();
    if (!canvasRect || !displayRect) return fullBounds;
    var canvasWidth = Number(canvasRect && canvasRect.width) || 0;
    var canvasHeight = Number(canvasRect && canvasRect.height) || 0;
    if (canvasWidth <= 0 || canvasHeight <= 0) return null;
    var canvasLeft = Number(canvasRect.left) || 0;
    var canvasTop = Number(canvasRect.top) || 0;
    var canvasRight = Number.isFinite(Number(canvasRect.right)) ? Number(canvasRect.right) : canvasLeft + canvasWidth;
    var canvasBottom = Number.isFinite(Number(canvasRect.bottom)) ? Number(canvasRect.bottom) : canvasTop + canvasHeight;
    var displayLeft = Number(displayRect && displayRect.left) || 0;
    var displayTop = Number(displayRect && displayRect.top) || 0;
    var displayRight = Number.isFinite(Number(displayRect && displayRect.right))
      ? Number(displayRect.right)
      : displayLeft + (Number(displayRect && displayRect.width) || 0);
    var displayBottom = Number.isFinite(Number(displayRect && displayRect.bottom))
      ? Number(displayRect.bottom)
      : displayTop + (Number(displayRect && displayRect.height) || 0);
    var visibleLeft = Math.max(canvasLeft, displayLeft);
    var visibleTop = Math.max(canvasTop, displayTop);
    var visibleRight = Math.min(canvasRight, displayRight);
    var visibleBottom = Math.min(canvasBottom, displayBottom);

    function clipsOverflow(value) {
      value = String(value || '').toLowerCase();
      return value === 'hidden' || value === 'clip' || value === 'scroll'
        || value === 'auto' || value === 'overlay';
    }

    function intersectVisibleRect(rect, clipX, clipY) {
      if (!rect) return;
      var rectLeft = Number(rect.left);
      var rectTop = Number(rect.top);
      var rectWidth = Number(rect.width);
      var rectHeight = Number(rect.height);
      if (!Number.isFinite(rectLeft)) rectLeft = 0;
      if (!Number.isFinite(rectTop)) rectTop = 0;
      var rectRight = Number(rect.right);
      var rectBottom = Number(rect.bottom);
      if (!Number.isFinite(rectRight)) rectRight = rectLeft + (Number.isFinite(rectWidth) ? rectWidth : 0);
      if (!Number.isFinite(rectBottom)) rectBottom = rectTop + (Number.isFinite(rectHeight) ? rectHeight : 0);
      if (clipX) {
        visibleLeft = Math.max(visibleLeft, rectLeft);
        visibleRight = Math.min(visibleRight, rectRight);
      }
      if (clipY) {
        visibleTop = Math.max(visibleTop, rectTop);
        visibleBottom = Math.min(visibleBottom, rectBottom);
      }
    }

    function elementOverflowClipRect(element) {
      var rect = element.getBoundingClientRect();
      if (!rect) return null;
      var rectWidth = Number(rect.width);
      var rectHeight = Number(rect.height);
      if (!Number.isFinite(rectWidth)) rectWidth = Number(rect.right) - Number(rect.left);
      if (!Number.isFinite(rectHeight)) rectHeight = Number(rect.bottom) - Number(rect.top);
      var offsetWidth = Number(element.offsetWidth);
      var offsetHeight = Number(element.offsetHeight);
      var clientWidth = Number(element.clientWidth);
      var clientHeight = Number(element.clientHeight);
      var scaleX = rectWidth > 0 && offsetWidth > 0 ? rectWidth / offsetWidth : 1;
      var scaleY = rectHeight > 0 && offsetHeight > 0 ? rectHeight / offsetHeight : 1;
      var left = Number(rect.left) || 0;
      var top = Number(rect.top) || 0;
      if (Number.isFinite(clientWidth) && clientWidth >= 0 && offsetWidth > 0) {
        left += (Number(element.clientLeft) || 0) * scaleX;
        rectWidth = clientWidth * scaleX;
      }
      if (Number.isFinite(clientHeight) && clientHeight >= 0 && offsetHeight > 0) {
        top += (Number(element.clientTop) || 0) * scaleY;
        rectHeight = clientHeight * scaleY;
      }
      return {
        left: left,
        top: top,
        right: left + (Number.isFinite(rectWidth) ? rectWidth : 0),
        bottom: top + (Number.isFinite(rectHeight) ? rectHeight : 0)
      };
    }

    // The stage is not necessarily the outermost clipping box. For example,
    // the rounded board also uses overflow:hidden, and responsive layouts can
    // put part of that board outside the viewport. Treat every actually visible
    // clipping edge as a hard flood-fill boundary.
    var view = canvas.ownerDocument && canvas.ownerDocument.defaultView
      ? canvas.ownerDocument.defaultView
      : window;
    var ancestor = canvas.parentElement;
    var ancestorDepth = 0;
    while (ancestor && ancestorDepth < 64) {
      if (ancestor !== displayArea && typeof ancestor.getBoundingClientRect === 'function') {
        var style = null;
        try {
          style = view && typeof view.getComputedStyle === 'function'
            ? view.getComputedStyle(ancestor)
            : null;
        } catch (_) {}
        var overflow = style && style.overflow;
        var clipX = clipsOverflow(style && (style.overflowX || overflow));
        var clipY = clipsOverflow(style && (style.overflowY || overflow));
        if (clipX || clipY) {
          var ancestorClipRect = null;
          try {
            ancestorClipRect = elementOverflowClipRect(ancestor);
          } catch (_) {}
          intersectVisibleRect(ancestorClipRect, clipX, clipY);
        }
      }
      ancestor = ancestor.parentElement;
      ancestorDepth += 1;
    }

    var documentElement = canvas.ownerDocument && canvas.ownerDocument.documentElement;
    var viewport = view && view.visualViewport;
    var hasVisualViewport = Number(viewport && viewport.width) > 0
      && Number(viewport && viewport.height) > 0;
    var viewportWidth = hasVisualViewport
      ? Number(viewport.width)
      : Number(view && view.innerWidth) || Number(documentElement && documentElement.clientWidth) || 0;
    var viewportHeight = hasVisualViewport
      ? Number(viewport.height)
      : Number(view && view.innerHeight) || Number(documentElement && documentElement.clientHeight) || 0;
    if (viewportWidth > 0 && viewportHeight > 0) {
      var viewportLeft = hasVisualViewport ? Number(viewport.offsetLeft) || 0 : 0;
      var viewportTop = hasVisualViewport ? Number(viewport.offsetTop) || 0 : 0;
      intersectVisibleRect({
        left: viewportLeft,
        top: viewportTop,
        right: viewportLeft + viewportWidth,
        bottom: viewportTop + viewportHeight
      }, true, true);
    }

    if (visibleRight <= visibleLeft || visibleBottom <= visibleTop) return null;
    return {
      minX: Math.max(0, Math.min(width - 1, Math.floor((visibleLeft - canvasLeft) * width / canvasWidth))),
      minY: Math.max(0, Math.min(height - 1, Math.floor((visibleTop - canvasTop) * height / canvasHeight))),
      maxX: Math.max(0, Math.min(width - 1, Math.ceil((visibleRight - canvasLeft) * width / canvasWidth) - 1)),
      maxY: Math.max(0, Math.min(height - 1, Math.ceil((visibleBottom - canvasTop) * height / canvasHeight) - 1))
    };
  }

  function floodFillPixelBuffer(data, width, height, startX, startY, fill, displayBounds) {
    width = Math.max(0, Math.floor(Number(width) || 0));
    height = Math.max(0, Math.floor(Number(height) || 0));
    if (!data || width < 1 || height < 1 || data.length < width * height * 4) return false;
    if (displayBounds === null) return false;
    displayBounds = displayBounds && typeof displayBounds === 'object' ? displayBounds : {};
    var boundaryMinX = Math.max(0, Math.min(width - 1, Math.floor(Number(displayBounds.minX) || 0)));
    var boundaryMinY = Math.max(0, Math.min(height - 1, Math.floor(Number(displayBounds.minY) || 0)));
    var boundaryMaxX = Math.max(boundaryMinX, Math.min(
      width - 1,
      Number.isFinite(Number(displayBounds.maxX)) ? Math.floor(Number(displayBounds.maxX)) : width - 1
    ));
    var boundaryMaxY = Math.max(boundaryMinY, Math.min(
      height - 1,
      Number.isFinite(Number(displayBounds.maxY)) ? Math.floor(Number(displayBounds.maxY)) : height - 1
    ));
    var requestedX = Math.floor(Number(startX) || 0);
    var requestedY = Math.floor(Number(startY) || 0);
    if (requestedX < boundaryMinX || requestedX > boundaryMaxX
        || requestedY < boundaryMinY || requestedY > boundaryMaxY) return false;
    var edgeIsBoundary = boundaryMaxX - boundaryMinX > 1 && boundaryMaxY - boundaryMinY > 1;
    var minX = edgeIsBoundary ? boundaryMinX + 1 : boundaryMinX;
    var minY = edgeIsBoundary ? boundaryMinY + 1 : boundaryMinY;
    var maxX = edgeIsBoundary ? boundaryMaxX - 1 : boundaryMaxX;
    var maxY = edgeIsBoundary ? boundaryMaxY - 1 : boundaryMaxY;
    var x = Math.max(minX, Math.min(maxX, requestedX));
    var y = Math.max(minY, Math.min(maxY, requestedY));
    var startPixel = y * width + x;
    var startIndex = startPixel * 4;
    var target = {
      r: data[startIndex],
      g: data[startIndex + 1],
      b: data[startIndex + 2],
      a: data[startIndex + 3]
    };
    if (pixelMatches(data, startIndex, fill)) return false;

    var visited = new Uint8Array(width * height);
    var stack = [startPixel];
    var filledAny = false;
    visited[startPixel] = 1;
    function queue(pixel) {
      if (visited[pixel]) return;
      visited[pixel] = 1;
      stack.push(pixel);
    }
    while (stack.length) {
      var pixel = stack.pop();
      var px = pixel % width;
      var py = Math.floor(pixel / width);
      var index = pixel * 4;
      if (!pixelMatches(data, index, target)) continue;
      setPixel(data, index, fill);
      visited[pixel] = 2;
      filledAny = true;
      if (px > minX) queue(pixel - 1);
      if (px < maxX) queue(pixel + 1);
      if (py > minY) queue(pixel - width);
      if (py < maxY) queue(pixel + width);
    }
    if (!filledAny || !edgeIsBoundary) return filledAny;

    // The outermost display pixels are a hard boundary during traversal. Once
    // the enclosed region is known, project its color onto the adjacent edge
    // without allowing that edge row/column to connect otherwise separate areas.
    function projectToEdge(edgePixel, innerPixel) {
      if (visited[innerPixel] !== 2) return;
      var edgeIndex = edgePixel * 4;
      if (pixelMatches(data, edgeIndex, target)) setPixel(data, edgeIndex, fill);
    }
    for (var edgeX = boundaryMinX + 1; edgeX < boundaryMaxX; edgeX += 1) {
      projectToEdge(boundaryMinY * width + edgeX, (boundaryMinY + 1) * width + edgeX);
      projectToEdge(boundaryMaxY * width + edgeX, (boundaryMaxY - 1) * width + edgeX);
    }
    for (var edgeY = boundaryMinY + 1; edgeY < boundaryMaxY; edgeY += 1) {
      projectToEdge(edgeY * width + boundaryMinX, edgeY * width + boundaryMinX + 1);
      projectToEdge(edgeY * width + boundaryMaxX, edgeY * width + boundaryMaxX - 1);
    }
    projectToEdge(boundaryMinY * width + boundaryMinX, (boundaryMinY + 1) * width + boundaryMinX + 1);
    projectToEdge(boundaryMinY * width + boundaryMaxX, (boundaryMinY + 1) * width + boundaryMaxX - 1);
    projectToEdge(boundaryMaxY * width + boundaryMinX, (boundaryMaxY - 1) * width + boundaryMinX + 1);
    projectToEdge(boundaryMaxY * width + boundaryMaxX, (boundaryMaxY - 1) * width + boundaryMaxX - 1);
    return true;
  }

  function floodFillCanvas(point) {
    var image;
    try {
      image = els.ctx.getImageData(0, 0, els.canvas.width, els.canvas.height);
    } catch (_) {
      return false;
    }
    var data = image.data;
    var width = image.width;
    var height = image.height;
    var fill = hexToRgba(currentBrushColor());
    var displayBounds = canvasDisplayPixelBounds(els.canvas, els.canvasStage);
    if (!displayBounds) return false;
    if (!floodFillPixelBuffer(data, width, height, point.x, point.y, fill, displayBounds)) return false;
    els.ctx.putImageData(image, 0, 0);
    return true;
  }

  function hideSizePreview() {
    clearTimeout(state.sizePreviewTimer);
    state.sizePreviewTimer = null;
    if (els.sizePreview) els.sizePreview.hidden = true;
    if (els.canvas) els.canvas.style.cursor = '';
  }

  function canvasCursorTool() {
    if (!isCanvasInteractionEnabled()) return '';
    if (state.brushMode === 'brush' && state.brushToolKind === 'bucket') return '';
    return state.brushMode === 'eraser' ? 'eraser' : 'brush';
  }

  function isPointerInsideCanvas(event) {
    if (!event || !els.canvas || !isCanvasInteractionEnabled()) return false;
    var rect = els.canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    return event.clientX >= rect.left
      && event.clientX <= rect.right
      && event.clientY >= rect.top
      && event.clientY <= rect.bottom;
  }

  function showSizePreview(tool, event) {
    if (!els.sizePreview || !els.canvas) return;
    var isCursorPreview = !!event;
    if (isCursorPreview && !isPointerInsideCanvas(event)) {
      hideSizePreview();
      return;
    }
    var kind = tool === 'eraser' ? 'eraser' : 'brush';
    var size = Number(kind === 'eraser' ? (els.eraserSize.value || 13) : (els.brushSize.value || 7));
    var canvasRect = els.canvas.getBoundingClientRect();
    var stageRect = els.canvasStage ? els.canvasStage.getBoundingClientRect() : canvasRect;
    if (!canvasRect.width || !canvasRect.height) return;
    var scaleX = canvasRect.width / Math.max(1, els.canvas.width || 800);
    var scaleY = canvasRect.height / Math.max(1, els.canvas.height || 600);
    var diameter = Math.max(2, size * ((scaleX + scaleY) / 2));
    var borderWidth = Math.max(1, Math.min(3, diameter / 5));
    els.sizePreview.dataset.previewTool = kind;
    els.sizePreview.dataset.previewMode = isCursorPreview ? 'cursor' : 'adjustment';
    var previewX = isCursorPreview ? event.clientX - stageRect.left : canvasRect.left + canvasRect.width / 2 - stageRect.left;
    var previewY = isCursorPreview ? event.clientY - stageRect.top : canvasRect.top + canvasRect.height / 2 - stageRect.top;
    els.sizePreview.style.setProperty('--dg-size-preview-x', previewX.toFixed(2) + 'px');
    els.sizePreview.style.setProperty('--dg-size-preview-y', previewY.toFixed(2) + 'px');
    els.sizePreview.style.setProperty('--dg-size-preview-diameter', diameter.toFixed(2) + 'px');
    els.sizePreview.style.setProperty('--dg-size-preview-border', borderWidth.toFixed(2) + 'px');
    if (kind === 'brush') {
      els.sizePreview.style.setProperty('--dg-size-preview-color', currentBrushColor());
    } else {
      els.sizePreview.style.removeProperty('--dg-size-preview-color');
    }
    els.sizePreview.hidden = false;
    if (isCursorPreview) {
      clearTimeout(state.sizePreviewTimer);
      state.sizePreviewTimer = null;
      els.canvas.style.cursor = 'none';
    } else {
      els.canvas.style.cursor = '';
      clearTimeout(state.sizePreviewTimer);
      state.sizePreviewTimer = setTimeout(hideSizePreview, 2600);
    }
  }

  function updateCanvasCursorPreview(event) {
    var tool = canvasCursorTool();
    if (!tool) {
      hideSizePreview();
      return;
    }
    showSizePreview(tool, event);
  }

  function setColorPanelPosition(left, top) {
    if (!els.colorPanel) return;
    var width = els.colorPanel.offsetWidth || 238;
    var height = els.colorPanel.offsetHeight || 360;
    var maxLeft = Math.max(8, window.innerWidth - width - 8);
    var maxTop = Math.max(8, window.innerHeight - height - 8);
    els.colorPanel.style.left = Math.max(8, Math.min(maxLeft, left)) + 'px';
    els.colorPanel.style.top = Math.max(8, Math.min(maxTop, top)) + 'px';
    els.colorPanel.style.right = 'auto';
  }

  function placeColorPanelNearToggle() {
    if (!els.colorPanel || !els.colorPanelToggle) return;
    var rect = els.colorPanelToggle.getBoundingClientRect();
    var panelWidth = els.colorPanel.offsetWidth || 238;
    setColorPanelPosition(rect.right - panelWidth, rect.bottom + 14);
  }

  function showColorPanel() {
    if (!els.colorPanel) return;
    closeToolPopovers();
    els.colorPanel.hidden = false;
    if (!els.colorPanel.style.left) {
      requestAnimationFrame(placeColorPanelNearToggle);
    }
    renderColorHistory();
  }

  function hideColorPanel() {
    if (els.colorPanel) els.colorPanel.hidden = true;
  }

  function toggleColorPanel() {
    if (!els.colorPanel || els.colorPanel.hidden) {
      showColorPanel();
    } else {
      hideColorPanel();
    }
  }

  function beginColorPanelDrag(event) {
    if (!els.colorPanel || !els.colorPanelHandle || event.button !== 0) return;
    if (event.target && event.target.closest && event.target.closest('button,input')) return;
    event.preventDefault();
    var rect = els.colorPanel.getBoundingClientRect();
    state.colorPanelDrag = {
      pointerId: event.pointerId,
      dx: event.clientX - rect.left,
      dy: event.clientY - rect.top
    };
    els.colorPanel.classList.add('is-dragging');
    try { els.colorPanelHandle.setPointerCapture(event.pointerId); } catch (_) {}
  }

  function moveColorPanelDrag(event) {
    if (!state.colorPanelDrag || state.colorPanelDrag.pointerId !== event.pointerId) return;
    event.preventDefault();
    setColorPanelPosition(event.clientX - state.colorPanelDrag.dx, event.clientY - state.colorPanelDrag.dy);
  }

  function endColorPanelDrag(event) {
    if (!state.colorPanelDrag || state.colorPanelDrag.pointerId !== event.pointerId) return;
    event.preventDefault();
    state.colorPanelDrag = null;
    if (els.colorPanel) els.colorPanel.classList.remove('is-dragging');
    try { els.colorPanelHandle.releasePointerCapture(event.pointerId); } catch (_) {}
  }

  function beginColorWheelPick(event) {
    if (!els.colorWheel || event.button !== 0) return;
    event.preventDefault();
    state.colorWheelPointerId = event.pointerId;
    pickColorFromWheel(event, false);
    try { els.colorWheel.setPointerCapture(event.pointerId); } catch (_) {}
  }

  function moveColorWheelPick(event) {
    if (state.colorWheelPointerId !== event.pointerId) return;
    event.preventDefault();
    pickColorFromWheel(event, false);
  }

  function endColorWheelPick(event) {
    if (state.colorWheelPointerId !== event.pointerId) return;
    event.preventDefault();
    pickColorFromWheel(event, true);
    state.colorWheelPointerId = null;
    try { els.colorWheel.releasePointerCapture(event.pointerId); } catch (_) {}
  }

  function requestEyeDropperColor() {
    if (window.EyeDropper) {
      try {
        new window.EyeDropper().open().then(function (result) {
          if (result && result.sRGBHex) setBrushColor(result.sRGBHex, { remember: true });
        }).catch(function () {});
        return;
      } catch (_) {}
    }
    if (els.brushColor) {
      els.brushColor.click();
    }
  }

  function configureStrokeContext() {
    els.ctx.lineCap = 'round';
    els.ctx.lineJoin = 'round';
    if (state.brushMode === 'eraser') {
      els.ctx.lineWidth = Number(els.eraserSize.value || 13);
      els.ctx.globalCompositeOperation = 'destination-out';
      els.ctx.strokeStyle = 'rgba(0,0,0,1)';
    } else {
      els.ctx.lineWidth = Number(els.brushSize.value || 7);
      els.ctx.globalCompositeOperation = 'source-over';
      els.ctx.strokeStyle = currentBrushColor();
    }
  }

  function activeStrokeSize() {
    return Number(state.brushMode === 'eraser' ? (els.eraserSize.value || 13) : (els.brushSize.value || 7));
  }

  function drawStrokePoint(point) {
    var radius = Math.max(0.5, activeStrokeSize() / 2);
    els.ctx.save();
    configureStrokeContext();
    els.ctx.fillStyle = state.brushMode === 'eraser' ? 'rgba(0,0,0,1)' : currentBrushColor();
    els.ctx.beginPath();
    els.ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    els.ctx.fill();
    els.ctx.restore();
  }

  function beginStroke(event) {
    if (!isCanvasInteractionEnabled() || event.button !== 0) return;
    event.preventDefault();
    updateCanvasCursorPreview(event);
    var p = canvasPoint(event);
    if (state.brushMode === 'brush' && state.brushToolKind === 'bucket') {
      if (floodFillCanvas(p)) {
        state.hasDrawn = true;
        pushHistory();
      }
      return;
    }
    state.isDrawing = true;
    drawStrokePoint(p);
    if (state.brushMode === 'brush') state.hasDrawn = true;
    configureStrokeContext();
    els.ctx.beginPath();
    els.ctx.moveTo(p.x, p.y);
    try { els.canvas.setPointerCapture(event.pointerId); } catch (_) {}
  }

  function moveStroke(event) {
    updateCanvasCursorPreview(event);
    if (!state.isDrawing || !isCanvasInteractionEnabled()) return;
    event.preventDefault();
    var p = canvasPoint(event);
    configureStrokeContext();
    els.ctx.lineTo(p.x, p.y);
    els.ctx.stroke();
    if (state.brushMode === 'brush') state.hasDrawn = true;
  }

  function endStroke(event) {
    if (!state.isDrawing) return;
    event.preventDefault();
    state.isDrawing = false;
    els.ctx.globalCompositeOperation = 'source-over';
    els.ctx.beginPath();
    try { els.canvas.releasePointerCapture(event.pointerId); } catch (_) {}
    pushHistory();
    updateCanvasCursorPreview(event);
  }

  function setTool(tool) {
    state.brushMode = tool;
    els.brushTool.setAttribute('aria-pressed', tool === 'brush' ? 'true' : 'false');
    els.eraserTool.setAttribute('aria-pressed', tool === 'eraser' ? 'true' : 'false');
    hideSizePreview();
  }

  function syncBrushToolButton() {
    var isBucket = state.brushToolKind === 'bucket';
    var key = isBucket ? 'drawingGuess.tools.bucket' : 'drawingGuess.tools.brush';
    var fallback = isBucket ? 'Bucket' : 'Brush';
    if (els.brushTool) {
      els.brushTool.dataset.brushKind = state.brushToolKind;
      els.brushTool.setAttribute('data-i18n-aria', key);
      els.brushTool.setAttribute('data-i18n-title', key);
      els.brushTool.setAttribute('aria-label', t(key, fallback));
      els.brushTool.title = t(key, fallback);
    }
  }

  function setBrushToolKind(kind) {
    var normalized = kind === 'bucket' ? 'bucket' : 'brush';
    state.brushToolKind = normalized;
    if (els.brushToolMenu) els.brushToolMenu.dataset.brushKind = normalized;
    if (els.brushModeBrush) els.brushModeBrush.setAttribute('aria-pressed', normalized === 'brush' ? 'true' : 'false');
    if (els.brushModeBucket) els.brushModeBucket.setAttribute('aria-pressed', normalized === 'bucket' ? 'true' : 'false');
    syncBrushToolButton();
    setTool('brush');
  }

  function closeToolPopovers(exceptPopover) {
    document.querySelectorAll('.dg-tool-popover.is-open').forEach(function (popover) {
      if (popover !== exceptPopover) popover.classList.remove('is-open');
    });
  }

  function openToolPopover(popover) {
    if (!popover) return;
    closeToolPopovers(popover);
    popover.classList.add('is-open');
    if (els.brushSize && popover.contains(els.brushSize) && state.brushToolKind === 'brush') {
      showSizePreview('brush');
    } else if (els.eraserSize && popover.contains(els.eraserSize)) {
      showSizePreview('eraser');
    } else {
      hideSizePreview();
    }
  }

  function bindToolPopoverEvents() {
    document.querySelectorAll('.dg-tool-popover').forEach(function (popover) {
      var closeTimer = null;
      function cancelClose() {
        clearTimeout(closeTimer);
        closeTimer = null;
      }
      function closeSoon() {
        clearTimeout(closeTimer);
        closeTimer = setTimeout(function () {
          closeTimer = null;
          popover.classList.remove('is-open');
        }, 180);
      }
      popover.addEventListener('pointerenter', function () {
        cancelClose();
        openToolPopover(popover);
      });
      popover.addEventListener('pointerleave', function () {
        closeSoon();
      });
      popover.addEventListener('focusin', function () {
        cancelClose();
        openToolPopover(popover);
      });
      popover.addEventListener('focusout', function (event) {
        if (event.relatedTarget && popover.contains(event.relatedTarget)) return;
        closeSoon();
      });
      popover.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        cancelClose();
        popover.classList.remove('is-open');
        var toolButton = popover.querySelector('.dg-tool');
        if (toolButton) toolButton.focus();
      });
    });
  }

  function playerTextPhaseAcceptsInput() {
    return !state.aiGuessTimeoutSettling
      && ['user_guessing', 'drawing_pick', 'user_drawing', 'ai_guess_feedback', 'summary', 'final_summary'].indexOf(state.phase) >= 0;
  }

  function dispatchPlayerText(value, options) {
    options = options || {};
    if (!state.routeActive || state.routeEnding || !playerTextPhaseAcceptsInput()) return Promise.resolve(false);
    addUserMessage(value);
    var request = null;
    if (state.phase === 'user_guessing') {
      request = submitUserGuess(value, options.inputMetadata);
    } else if (state.phase === 'drawing_pick') {
      request = submitGameChat(value, { inputMetadata: options.inputMetadata });
    } else if (state.phase === 'user_drawing') {
      request = submitGameChat(value, { inputMetadata: options.inputMetadata });
    } else if (state.phase === 'ai_guess_feedback') {
      request = submitFeedbackInput(value, options.inputMetadata);
    } else if (state.phase === 'summary') {
      request = submitGameChat(value, { inputMetadata: options.inputMetadata });
    } else if (state.phase === 'final_summary') {
      request = submitGameChat(value, { summaryChatOnly: true, inputMetadata: options.inputMetadata });
    }
    return Promise.resolve(request).then(function () { return true; });
  }

  function submitPlayerText(value, options) {
    options = options || {};
    value = String(value || '').trim();
    if (!value || !state.routeActive || state.routeEnding || !playerTextPhaseAcceptsInput()) return false;
    var queueGeneration = state.playerTextQueueGeneration;
    var queuedRoundToken = state.roundFlowToken;
    var queuedPhase = state.phase;
    var queuedOptions = {
      summaryChatOnly: options.summaryChatOnly === true,
      inputMetadata: options.inputMetadata && typeof options.inputMetadata === 'object'
        ? Object.assign({}, options.inputMetadata)
        : options.inputMetadata
    };
    // Typed and SDK voice input share one command queue. SpeechRecognition can
    // emit several final utterances while a 20-second round command is still
    // running; serializing here preserves what the player said and prevents
    // the shared chatInFlight flag from being cleared by the wrong request.
    state.playerTextChain = Promise.resolve(state.playerTextChain).catch(function () {})
      .then(function () {
        if (queueGeneration !== state.playerTextQueueGeneration
          || queuedRoundToken !== state.roundFlowToken
          || queuedPhase !== state.phase) return false;
        return dispatchPlayerText(value, queuedOptions);
      }).catch(function () {
        if (queueGeneration === state.playerTextQueueGeneration && state.routeActive && !state.routeEnding) {
          addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        }
        return false;
      });
    return true;
  }

  function handleChatSubmit(event) {
    event.preventDefault();
    var value = String(els.chatInput.value || '').trim();
    if (!value) return;
    if (submitPlayerText(value)) els.chatInput.value = '';
  }

  function handleVoiceRouteButton() {
    if (!state.routeActive) {
      addEventMessage('drawingGuess.voice.routeNotReady', 'Wait until the game route is ready before using voice.');
      return;
    }
    if (state.voiceControlPending) return;
    var client = state.sdkClient;
    if (!client || !hasVoiceInputCapability()) {
      addEventMessage('drawingGuess.voice.unavailable', 'In-game voice is unavailable in this environment.');
      return;
    }
    var wasActive = state.voiceRouteActive;
    var requestSequence = state.voiceControlRequestSequence + 1;
    state.voiceControlRequestSequence = requestSequence;
    state.voiceControlPending = true;
    updateControls();
    client.voice.toggle({ timeoutMs: 12000 }).then(function (voiceState) {
      if (requestSequence !== state.voiceControlRequestSequence) return;
      handleSdkVoiceState(voiceState);
      if (voiceState && voiceState.ok === false) {
        addEventMessage('drawingGuess.voice.controlFailed', 'Voice operation failed: {{reason}}', {
          reason: String(voiceState.reason || 'request_failed')
        });
        return;
      }
      var active = voiceState && typeof voiceState.active === 'boolean'
        ? voiceState.active
        : !wasActive;
      state.voiceRouteActive = active;
      addEventMessage(
        active ? 'drawingGuess.voice.connectedNotice' : 'drawingGuess.voice.connectHintNotice',
        active ? 'Voice is on for this round.' : 'Voice is off for this round.'
      );
    }).catch(function (error) {
      if (requestSequence !== state.voiceControlRequestSequence) return;
      addEventMessage('drawingGuess.voice.controlFailed', 'Voice operation failed: {{reason}}', {
        reason: sdkErrorReason(error)
      });
    }).finally(function () {
      if (requestSequence !== state.voiceControlRequestSequence) return;
      state.voiceControlPending = false;
      updateControls();
    });
  }

  function finishGame() {
    if (!state.roundSessionReady) return false;
    renderFinalSummary();
    showExitConfirm();
    return true;
  }

  function bindEvents() {
    els.tutorialStartButton.addEventListener('click', startGame);
    els.nextRoundButton.addEventListener('click', startNextRound);
    els.endButton.addEventListener('click', finishGame);
    if (els.exitStayButton) els.exitStayButton.addEventListener('click', deferExitConfirm);
    if (els.exitLeaveButton) els.exitLeaveButton.addEventListener('click', leaveDrawingGuessPage);
    if (els.exitReopenButton) els.exitReopenButton.addEventListener('click', showExitConfirm);
    els.doneButton.addEventListener('click', function () { submitDrawing(true); });
    els.clearCanvasButton.addEventListener('click', function () {
      if (isCanvasEditablePhase()) {
        resetCanvas();
        pushCanvasContextForRoute(true);
        addMessage('drawingGuess.messages.canvasCleared', 'Canvas cleared.');
      }
    });
    els.chatForm.addEventListener('submit', handleChatSubmit);
    if (els.voiceRouteButton) els.voiceRouteButton.addEventListener('click', handleVoiceRouteButton);
    els.brushTool.addEventListener('click', function () { setTool('brush'); });
    if (els.brushModeBrush) els.brushModeBrush.addEventListener('click', function () { setBrushToolKind('brush'); });
    if (els.brushModeBucket) els.brushModeBucket.addEventListener('click', function () { setBrushToolKind('bucket'); });
    els.eraserTool.addEventListener('click', function () { setTool('eraser'); });
    if (els.brushSize) {
      els.brushSize.addEventListener('pointerdown', function () { showSizePreview('brush'); });
      els.brushSize.addEventListener('focus', function () { showSizePreview('brush'); });
      els.brushSize.addEventListener('input', function () { showSizePreview('brush'); });
    }
    if (els.eraserSize) {
      els.eraserSize.addEventListener('pointerdown', function () { showSizePreview('eraser'); });
      els.eraserSize.addEventListener('focus', function () { showSizePreview('eraser'); });
      els.eraserSize.addEventListener('input', function () { showSizePreview('eraser'); });
    }
    if (els.colorPanelToggle) els.colorPanelToggle.addEventListener('click', toggleColorPanel);
    if (els.eyedropperButton) els.eyedropperButton.addEventListener('click', requestEyeDropperColor);
    if (els.colorPanelClose) els.colorPanelClose.addEventListener('click', hideColorPanel);
    if (els.colorPanelHandle) {
      els.colorPanelHandle.addEventListener('pointerdown', beginColorPanelDrag);
      els.colorPanelHandle.addEventListener('pointermove', moveColorPanelDrag);
      els.colorPanelHandle.addEventListener('pointerup', endColorPanelDrag);
      els.colorPanelHandle.addEventListener('pointercancel', endColorPanelDrag);
    }
    if (els.colorWheel) {
      els.colorWheel.addEventListener('pointerdown', beginColorWheelPick);
      els.colorWheel.addEventListener('pointermove', moveColorWheelPick);
      els.colorWheel.addEventListener('pointerup', endColorWheelPick);
      els.colorWheel.addEventListener('pointercancel', endColorWheelPick);
    }
    if (els.colorPanel) {
      els.colorPanel.addEventListener('click', function (event) {
        var chip = event.target && event.target.closest ? event.target.closest('[data-color]') : null;
        if (chip) setBrushColor(chip.dataset.color, { remember: true });
      });
    }
    if (els.brushColor) {
      els.brushColor.addEventListener('input', function () {
        setBrushColor(els.brushColor.value);
      });
      els.brushColor.addEventListener('change', function () {
        setBrushColor(els.brushColor.value, { remember: true });
      });
    }
    bindToolPopoverEvents();
    els.undoTool.addEventListener('click', undoCanvas);
    els.redoTool.addEventListener('click', redoCanvas);
    if (els.modelStage) {
      els.modelStage.addEventListener('wheel', handleModelWheel, { passive: false });
      els.modelStage.addEventListener('pointerdown', beginModelDrag);
      els.modelStage.addEventListener('pointermove', moveModelDrag);
      els.modelStage.addEventListener('pointerup', endModelDrag);
      els.modelStage.addEventListener('pointercancel', endModelDrag);
    }
    if (els.modelResetControl) {
      els.modelResetControl.addEventListener('click', resetModelView);
    }
    if (els.sideResizer) {
      els.sideResizer.addEventListener('pointerdown', beginSideResize);
      els.sideResizer.addEventListener('pointermove', moveSideResize);
      els.sideResizer.addEventListener('pointerup', endSideResize);
      els.sideResizer.addEventListener('pointercancel', endSideResize);
      els.sideResizer.addEventListener('keydown', handleSideResizeKey);
    }
    els.canvas.addEventListener('pointerdown', beginStroke);
    els.canvas.addEventListener('pointermove', moveStroke);
    els.canvas.addEventListener('pointerup', endStroke);
    els.canvas.addEventListener('pointercancel', endStroke);
    els.canvas.addEventListener('pointerenter', updateCanvasCursorPreview);
    els.canvas.addEventListener('pointerleave', hideSizePreview);
    document.querySelectorAll('input[name="memory-consent"]').forEach(function (input) {
      input.addEventListener('change', readMemoryConsent);
    });
    window.addEventListener('resize', function () {
      requestAnimationFrame(function () {
        applySideSplitRatio(state.sideSplitRatio, false);
      });
    });
    window.addEventListener('pageshow', function (event) {
      // The SDK intentionally disposes on pagehide. A BFCache restoration would
      // otherwise reuse that disposed client and resolved connect promise.
      if (event && event.persisted) window.location.reload();
    });
  }

  function init() {
    initEls();
    loadColorHistory();
    setBrushColor(currentBrushColor());
    renderColorHistory();
    syncBrushToolButton();
    state.sessionId = String(boot.sessionId || '') || makeSessionId();
    state.lanlanName = String(boot.lanlanName || '').trim();
    state.windowLanlanName = state.lanlanName;
    syncPageLocale();
    window.addEventListener('localechange', syncPageLocale);
    loadModelViewSettings();
    loadSideSplitRatio();
    resetCanvas();
    showPlaceholder();
    setPhase('tutorial');
    readMemoryConsent();
    bindEvents();
    connectMiniGameSdk().catch(function (error) {
      console.warn('[DrawingGuessSDK] SDK connection unavailable:', sdkErrorReason(error));
    });
    loadCurrentCharacter();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
