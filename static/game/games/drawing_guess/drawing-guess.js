(function () {
  'use strict';

  var GAME_TYPE = 'drawing_guess';
  var ROUTE_API = '/api/game/' + GAME_TYPE;
  var ROUND_API = '/api/game/drawing_guess';
  var ROUND_FALLBACK_SECONDS = 5 * 60;
  var AI_DRAW_REQUEST_TIMEOUT_MS = 70 * 1000;
  var AI_GUESS_REQUEST_TIMEOUT_MS = ROUND_FALLBACK_SECONDS * 1000 + 10000;
  var AI_GUESS_TIMEOUT_MAX_RETRIES = 2;
  var AI_GUESS_TIMEOUT_BUSY_MAX_POLLS = 50;
  var AI_GUESS_MIN_DELAY_MS = 10000;
  var AI_GUESS_MAX_DELAY_MS = 60000;
  var DRAW_PICK_DURATION_MS = 1450;
  var AI_DRAWING_PLACEHOLDER_DELAY_MS = 1200;
  var COLOR_HISTORY_VISIBLE_COUNT = 7;
  var COLOR_HISTORY_MAX_COUNT = 28;
  var boot = window.__DRAWING_GUESS_BOOT__ || {};

  var state = {
    sessionId: '',
    lanlanName: '',
    windowLanlanName: '',
    routeActive: false,
    routeEnding: false,
    heartbeatTimer: null,
    routeDrainTimer: null,
    routeDrainInFlight: false,
    countdownTimer: null,
    thinkingTimer: null,
    placeholderDotsTimer: null,
    aiDrawingPlaceholderTimer: null,
    sizePreviewTimer: null,
    colorPanelDrag: null,
    debugPanelDrag: null,
    colorWheelPointerId: null,
    colorHistory: [],
    drawPickTimer: null,
    drawPickRevealTimer: null,
    aiGuessTimer: null,
    aiGuessNextAt: 0,
    nekoVoiceQueue: [],
    nekoVoiceInFlight: false,
    speechAudioSocket: null,
    speechAudioTapReady: false,
    speechAudioReconnectTimer: null,
    speechAudioPingTimer: null,
    speechAudioManualClose: false,
    lipSyncActive: false,
    lipSyncAnalyser: null,
    lipSyncDriver: '',
    lipSyncFrame: null,
    lipSyncRetryTimer: null,
    lipSyncStopTimer: null,
    lipSyncRetryDeadline: 0,
    lipSyncMouthOpen: 0,
    voiceRouteActive: false,
    voiceRouteStatusNotified: false,
    lastExternalInputRequestId: '',
    canvasContextLastHash: '',
    canvasContextLastSentAt: 0,
    thinkingMessageNode: null,
    modelMoodTimer: null,
    modelResizeHandler: null,
    debugGesture: [],
    debugGestureTimer: null,
    debugCountdownTimer: null,
    debugCharactersLoaded: false,
    debugRotateRounds: true,
    debugRoundMode: 'auto',
    debugWordCycle: null,
    roundFlowToken: 0,
    activeRoundToken: 0,
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
    aiGuessAttempts: 0,
    maxAiGuessAttempts: 3,
    phase: 'tutorial',
    memoryConsent: 'none',
    aiSvg: '',
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
      scale: 190,
      x: 0,
      y: 28
    },
    modelDrag: null,
    sideSplitRatio: 0.64,
    sideResize: null,
    modelFitBase: null,
    live2dMood: '',
    live2dMouthParameterId: '',
    live2dManager: null,
    vrmManager: null,
    mmdManager: null,
    pngtuberManager: null,
    recentNekoMessages: [],
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
      debugTrigger: $('debug-trigger'),
      debugPanel: $('debug-panel'),
      debugPanelHandle: $('debug-panel-handle'),
      debugClose: $('debug-close'),
      debugCharacterSelect: $('debug-character-select'),
      debugAiRound: $('debug-ai-round'),
      debugUserRound: $('debug-user-round'),
      debugRotateRounds: $('debug-rotate-rounds'),
      debugAiGuessCountdown: $('debug-ai-guess-countdown'),
      debugWordPool1Count: $('debug-word-pool1-count'),
      debugWordPool1Lock: $('debug-word-pool1-lock'),
      debugWordPool2Count: $('debug-word-pool2-count'),
      debugWordPool2Lock: $('debug-word-pool2-lock'),
      debugSessionLock: $('debug-session-lock'),
      debugTriggerAiGuess: $('debug-trigger-ai-guess'),
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
    var candidates = [];
    try { candidates.push(window.i18next && window.i18next.language); } catch (_) {}
    try { candidates.push(window.__nekoI18nLanguage || window.NEKO_I18N_LANGUAGE); } catch (_) {}
    try { candidates.push(localStorage.getItem('neko_i18n_language')); } catch (_) {}
    for (var i = 0; i < candidates.length; i += 1) {
      var value = String(candidates[i] || '').trim();
      if (value) return value;
    }
    return document.documentElement.lang || 'zh-CN';
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
    scale: 190,
    x: 0,
    y: 28
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

  function modelViewStorageKey() {
    return 'drawingGuess.modelView.' + encodeURIComponent(state.lanlanName || 'default');
  }

  function sideSplitStorageKey() {
    return 'drawingGuess.sideSplitRatio';
  }

  function applyModelView() {
    var view = normalizeModelView(state.modelView);
    state.modelView = view;
    if (els.modelStage) {
      els.modelStage.style.setProperty('--dg-model-scale', String(view.scale / 100));
      els.modelStage.style.setProperty('--dg-model-offset-x', view.x + '%');
      els.modelStage.style.setProperty('--dg-model-offset-y', view.y + '%');
    }
    if (state.modelKind === 'live2d') {
      fitLive2DModelToSlot();
    } else if (state.modelKind === 'vrm' || state.modelKind === 'mmd') {
      resizeActiveModelRenderer();
    }
  }

  function setModelView(nextView, shouldSave) {
    state.modelView = normalizeModelView(Object.assign({}, state.modelView, nextView || {}));
    applyModelView();
    if (shouldSave) saveModelViewSettings();
  }

  function saveModelViewSettings() {
    try {
      localStorage.setItem(modelViewStorageKey(), JSON.stringify(normalizeModelView(state.modelView)));
    } catch (_) {}
  }

  function loadModelViewSettings() {
    var loaded = null;
    try {
      loaded = JSON.parse(localStorage.getItem(modelViewStorageKey()) || 'null');
    } catch (_) {
      loaded = null;
    }
    if (loaded
      && Number(loaded.scale) === 100
      && Number(loaded.x) === 0
      && Number(loaded.y) === 0) {
      loaded = null;
    }
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
    if (shouldSave) {
      try { localStorage.setItem(sideSplitStorageKey(), String(normalized)); } catch (_) {}
    }
  }

  function loadSideSplitRatio() {
    var value = null;
    try { value = localStorage.getItem(sideSplitStorageKey()); } catch (_) { value = null; }
    applySideSplitRatio(value == null ? SIDE_SPLIT_DEFAULT_RATIO : Number(value), false);
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
    if (state.modelKind === 'live2d' && state.modelFitBase) {
      return {
        width: Math.max(1, Math.min(width, state.modelFitBase.width || width)),
        height: Math.max(1, Math.min(height, state.modelFitBase.height || height))
      };
    }
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
    applyLive2DMood(normalized);
    applyPNGTuberMood(normalized);
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

  function applyLive2DMood(mood) {
    if (state.modelKind !== 'live2d') return;
    var manager = state.live2dManager;
    if (!manager || !manager.currentModel || typeof manager.setEmotion !== 'function') return;
    if (manager.isEmotionChanging) return;
    var emotionMap = {
      idle: 'Idle',
      drawing: 'thinking',
      thinking: 'thinking',
      guessing: 'thinking',
      talking: 'happy',
      happy: 'happy'
    };
    var emotion = emotionMap[mood] || 'Idle';
    if (state.live2dMood === emotion) return;
    state.live2dMood = emotion;
    Promise.resolve(manager.setEmotion(emotion)).catch(function () {});
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
    requestAnimationFrame(function () {
      if (state.modelKind === 'live2d') {
        fitLive2DModelToSlot();
        return;
      }
      if (state.modelKind === 'vrm') {
        applyEmbeddedModelSlotStyles('vrm');
        if (state.vrmManager && typeof state.vrmManager.onWindowResize === 'function') {
          state.vrmManager.onWindowResize();
        }
        return;
      }
      if (state.modelKind === 'mmd') {
        applyEmbeddedModelSlotStyles('mmd');
        if (state.mmdManager && typeof state.mmdManager.onWindowResize === 'function') {
          state.mmdManager.onWindowResize();
        }
        return;
      }
      if (state.modelKind === 'pngtuber') {
        applyEmbeddedModelSlotStyles('pngtuber');
      }
    });
  }

  function applyPNGTuberMood(mood) {
    if (state.modelKind !== 'pngtuber') return;
    var manager = state.pngtuberManager;
    if (!manager || typeof manager.setState !== 'function') return;
    var stateMap = {
      idle: 'idle',
      drawing: 'idle',
      thinking: 'idle',
      guessing: 'idle',
      talking: 'talking',
      happy: 'happy'
    };
    try {
      manager.setState(stateMap[mood] || 'idle');
    } catch (_) {}
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

  function syncVoiceRouteButton() {
    if (!els.voiceRouteButton) return;
    var active = !!state.voiceRouteActive;
    els.voiceRouteButton.disabled = !state.routeActive || state.routeEnding;
    els.voiceRouteButton.classList.toggle('is-active', active);
    els.voiceRouteButton.setAttribute('aria-pressed', active ? 'true' : 'false');
    if (state.voiceRouteActive) {
      els.voiceRouteButton.title = t('drawingGuess.voice.connected', 'Voice is connected to this round.');
    } else {
      els.voiceRouteButton.title = t('drawingGuess.voice.connectHint', 'Open voice on the main page to let this round take it over.');
    }
    if (els.voiceRouteIcon) {
      els.voiceRouteIcon.src = active ? '/static/icons/mic_icon_on.png' : '/static/icons/mic_icon_off.png';
    }
  }

  function updateControls() {
    var routeReady = !!state.lanlanName && !state.routeEnding;
    var tutorialOpen = !!els.tutorialOverlay && !els.tutorialOverlay.hidden;
    var canvasEditable = isCanvasEditablePhase();
    var roundSummaryOpen = state.phase === 'summary';
    var finalSummaryOpen = state.phase === 'final_summary';
    els.characterName.textContent = state.lanlanName || '-';
    els.sessionId.textContent = state.sessionId || '-';
    els.memoryState.setAttribute('data-i18n', 'drawingGuess.memory.' + state.memoryConsent + 'Short');
    els.memoryState.textContent = t('drawingGuess.memory.' + state.memoryConsent + 'Short', state.memoryConsent);
    els.doneButton.hidden = roundSummaryOpen || finalSummaryOpen;
    els.nextRoundButton.hidden = !roundSummaryOpen;
    els.endButton.hidden = finalSummaryOpen;
    els.endButton.disabled = !state.routeActive || state.routeEnding;
    els.doneButton.disabled = tutorialOpen || !routeReady || !canvasEditable;
    els.clearCanvasButton.disabled = !canvasEditable;
    els.nextRoundButton.disabled = state.phase !== 'summary' || !state.routeActive;
    els.chatSubmit.disabled = state.phase !== 'user_guessing' && state.phase !== 'drawing_pick' && state.phase !== 'user_drawing' && state.phase !== 'ai_guess_feedback' && state.phase !== 'summary' && state.phase !== 'final_summary';
    els.chatInput.disabled = els.chatSubmit.disabled;
    els.undoTool.disabled = !canvasEditable || state.history.length <= 1;
    els.redoTool.disabled = !canvasEditable || state.redo.length === 0;
    if (!canvasEditable || (state.brushMode === 'brush' && state.brushToolKind === 'bucket')) hideSizePreview();
    syncVoiceRouteButton();
    syncDebugPanelState();
  }

  function isDebugAiGuessAvailable() {
    return ['user_drawing', 'ai_guessing', 'ai_guess_feedback'].indexOf(state.phase) >= 0;
  }

  function formatDebugCountdown(ms) {
    if (!Number.isFinite(ms) || ms <= 0) return '0s';
    var seconds = Math.ceil(ms / 1000);
    var minutes = Math.floor(seconds / 60);
    var rest = seconds % 60;
    if (minutes <= 0) return seconds + 's';
    return minutes + ':' + String(rest).padStart(2, '0');
  }

  function applyRoundState(roundState) {
    if (!roundState || typeof roundState !== 'object') return;
    if (roundState.word_cycle && typeof roundState.word_cycle === 'object') {
      state.debugWordCycle = roundState.word_cycle;
      syncDebugPanelState();
    }
  }

  function debugPoolLabel(pool, field) {
    var wordCycle = state.debugWordCycle || {};
    var pools = wordCycle.pools || {};
    var data = pools[pool] || {};
    if (field === 'count') {
      var count = Number(data.remaining_count);
      return Number.isFinite(count) ? String(count) : '--';
    }
    if (field === 'lock') {
      if (!wordCycle.active_pool) return '--';
      return data.locked
        ? t('drawingGuess.debug.locked', 'Locked')
        : t('drawingGuess.debug.available', 'Available');
    }
    return '--';
  }

  function isDebugPoolLocked(pool) {
    var wordCycle = state.debugWordCycle || {};
    var pools = wordCycle.pools || {};
    return !!wordCycle.active_pool && !!((pools[pool] || {}).locked);
  }

  function updateDebugWordCycle() {
    if (els.debugWordPool1Count) els.debugWordPool1Count.textContent = debugPoolLabel('pool1', 'count');
    if (els.debugWordPool1Lock) {
      var pool1Lock = debugPoolLabel('pool1', 'lock');
      els.debugWordPool1Lock.textContent = pool1Lock;
      els.debugWordPool1Lock.dataset.locked = String(isDebugPoolLocked('pool1'));
    }
    if (els.debugWordPool2Count) els.debugWordPool2Count.textContent = debugPoolLabel('pool2', 'count');
    if (els.debugWordPool2Lock) {
      var pool2Lock = debugPoolLabel('pool2', 'lock');
      els.debugWordPool2Lock.textContent = pool2Lock;
      els.debugWordPool2Lock.dataset.locked = String(isDebugPoolLocked('pool2'));
    }
    if (els.debugSessionLock) {
      var locked = !!(state.debugWordCycle && state.debugWordCycle.request_locked);
      els.debugSessionLock.textContent = locked
        ? t('drawingGuess.debug.requestLocked', 'Request locked')
        : t('drawingGuess.debug.requestUnlocked', 'Unlocked');
      els.debugSessionLock.dataset.locked = String(locked);
    }
  }

  function updateDebugGuessCountdown() {
    if (!els.debugAiGuessCountdown) return;
    var text = '--';
    if (state.phase === 'ai_guess_feedback') {
      if (state.pendingAutoGuess && state.chatInFlight) {
        text = t('drawingGuess.debug.waitingForChat', 'Waiting for chat');
      } else if (state.aiGuessInFlight) {
        text = t('drawingGuess.debug.guessing', 'Guessing');
      } else if (state.aiGuessTimer && state.aiGuessNextAt) {
        text = formatDebugCountdown(state.aiGuessNextAt - Date.now());
      } else if (state.aiGuessAttempts >= state.maxAiGuessAttempts) {
        text = t('drawingGuess.debug.attemptsExhausted', 'No attempts left');
      } else {
        text = t('drawingGuess.debug.notScheduled', 'Not scheduled');
      }
    }
    els.debugAiGuessCountdown.textContent = text;
  }

  function syncDebugPanelState() {
    if (els.debugRotateRounds) {
      els.debugRotateRounds.checked = !!state.debugRotateRounds;
    }
    if (els.debugAiRound) {
      els.debugAiRound.setAttribute('aria-pressed', !state.debugRotateRounds && state.debugRoundMode === 'ai' ? 'true' : 'false');
    }
    if (els.debugUserRound) {
      els.debugUserRound.setAttribute('aria-pressed', !state.debugRotateRounds && state.debugRoundMode === 'user' ? 'true' : 'false');
    }
    if (els.debugCharacterSelect && state.lanlanName) {
      els.debugCharacterSelect.value = state.lanlanName;
    }
    if (els.debugTriggerAiGuess) {
      els.debugTriggerAiGuess.disabled = !isDebugAiGuessAvailable() || state.aiGuessInFlight;
    }
    updateDebugWordCycle();
    updateDebugGuessCountdown();
  }

  function startDebugCountdownUpdater() {
    clearInterval(state.debugCountdownTimer);
    state.debugCountdownTimer = setInterval(updateDebugGuessCountdown, 300);
  }

  function beginRoundFlow() {
    clearTimeout(state.guessTimeoutRetryTimer);
    state.guessTimeoutRetryTimer = null;
    clearTimeout(state.aiGuessTimeoutRetryTimer);
    state.aiGuessTimeoutRetryTimer = null;
    state.roundFlowToken += 1;
    return state.roundFlowToken;
  }

  function isCurrentRoundFlow(token) {
    return token === state.roundFlowToken;
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
    var host = window.nekoHost;
    if (host && typeof host.closeWindow === 'function') {
      try {
        Promise.resolve(host.closeWindow())
          .then(function (result) {
            if (result && result.ok === false) {
              closeDrawingGuessBrowserFallback();
            }
          })
          .catch(function () {
            closeDrawingGuessBrowserFallback();
          });
        return;
      } catch (_) {}
    }
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

  function shakeDebugTrigger() {
    if (!els.debugTrigger) return;
    els.debugTrigger.classList.remove('is-shaking');
    void els.debugTrigger.offsetWidth;
    els.debugTrigger.classList.add('is-shaking');
  }

  function recordDebugGesture(step) {
    clearTimeout(state.debugGestureTimer);
    state.debugGesture.push(step);
    state.debugGesture = state.debugGesture.slice(-4);
    if (state.debugGesture.join('') === 'LLRR') {
      state.debugGesture = [];
      openDebugPanel();
      return;
    }
    state.debugGestureTimer = setTimeout(function () {
      state.debugGesture = [];
    }, 1800);
  }

  function openDebugPanel() {
    if (!els.debugPanel) return;
    els.debugPanel.hidden = false;
    loadDebugCharacters();
    syncDebugPanelState();
  }

  function closeDebugPanel() {
    if (els.debugPanel) els.debugPanel.hidden = true;
  }

  function loadDebugCharacters() {
    if (!els.debugCharacterSelect || state.debugCharactersLoaded) return;
    fetch('/api/characters', { cache: 'no-store' }).then(function (res) {
      if (!res.ok) throw new Error('characters_fetch_failed_' + res.status);
      return res.json();
    }).then(function (data) {
      var characters = data && data['猫娘'];
      var names = Object.keys(characters || {}).sort(function (a, b) {
        return a.localeCompare(b);
      });
      if (!names.length && state.lanlanName) names = [state.lanlanName];
      els.debugCharacterSelect.innerHTML = names.map(function (name) {
        return '<option value="' + escapeAttr(name) + '">' + escapeHtml(name) + '</option>';
      }).join('');
      state.debugCharactersLoaded = true;
      syncDebugPanelState();
    }).catch(function () {
      var fallbackName = state.lanlanName || '';
      els.debugCharacterSelect.innerHTML = fallbackName
        ? '<option value="' + escapeAttr(fallbackName) + '">' + escapeHtml(fallbackName) + '</option>'
        : '<option value="">角色列表读取失败</option>';
      syncDebugPanelState();
    });
  }

  function endRouteForDebugSwitch(lanlanName, sessionId) {
    if (!lanlanName || !sessionId) return Promise.resolve();
    clearInterval(state.heartbeatTimer);
    stopRouteDrain();
    var payload = {
      session_id: sessionId,
      lanlan_name: lanlanName,
      source: 'drawing_guess',
      game_type: GAME_TYPE,
      i18n_language: currentLanguage(),
      reason: 'drawing_guess_debug_character_switch',
      roundCompleted: false,
      round_completed: false,
      postgameProactive: false,
      window_lanlan_name: state.windowLanlanName || lanlanName,
      suppressWindowStateChange: true,
      suppressRouteEndStatus: true
    };
    return fetch(ROUTE_API + '/route/end', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).catch(function () {});
  }

  function switchDebugCharacter(name) {
    var nextName = String(name || '').trim();
    if (!nextName || nextName === state.lanlanName) return Promise.resolve();
    var oldName = state.lanlanName;
    var oldSessionId = state.sessionId;
    var previousDebugMode = state.debugRoundMode;
    var previousRotateRounds = state.debugRotateRounds;
    var shouldContinueRound = state.phase !== 'tutorial' && state.phase !== 'ended' && state.phase !== 'final_summary';
    if (!state.windowLanlanName) state.windowLanlanName = oldName;
    return endRouteForDebugSwitch(oldName, oldSessionId).finally(function () {
      clearNekoVoiceQueue();
      stopSpeechAudioSocket();
      stopCountdown();
      stopDrawPickAnimation();
      stopAiGuessSchedule();
      stopThinkingEventMessage();
      state.routeActive = false;
      state.routeEnding = false;
      state.voiceRouteActive = false;
      state.voiceRouteStatusNotified = false;
      state.lastExternalInputRequestId = '';
      state.canvasContextLastHash = '';
      state.canvasContextLastSentAt = 0;
      state.roundFlowToken += 1;
      state.sessionId = makeSessionId();
      state.lanlanName = nextName;
      state.debugRoundMode = previousDebugMode || 'auto';
      state.debugRotateRounds = previousRotateRounds;
      window.lanlan_config = window.lanlan_config || {};
      window.lanlan_config.lanlan_name = nextName;
      loadModelViewSettings();
      initModelSlotForCurrentCharacter(nextName).catch(function () {});
      showPlaceholder();
      setPhase('loading_round');
      addEventMessage(
        shouldContinueRound ? 'drawingGuess.debug.switchedCharacterContinue' : 'drawingGuess.debug.switchedCharacter',
        shouldContinueRound
          ? 'Debug: switched to {{name}}, continuing the current test round.'
          : 'Debug: switched to {{name}}.',
        { name: nextName }
      );
      return startRoute().then(function (ok) {
        if (!ok || !shouldContinueRound) return ok;
        if (!state.debugRotateRounds && state.debugRoundMode === 'user') return startDebugUserRound(true);
        if (!state.debugRotateRounds && state.debugRoundMode === 'ai') return startDebugAiRound(true);
        return startRound();
      });
    }).finally(function () {
      syncDebugPanelState();
      updateControls();
    });
  }

  function ensureDebugRouteReady() {
    readMemoryConsent();
    if (els.tutorialOverlay) els.tutorialOverlay.hidden = true;
    if (state.routeActive) return Promise.resolve(true);
    return startRoute();
  }

  function startDebugAiRound(keepMode) {
    if (!keepMode) state.debugRoundMode = 'ai';
    return ensureDebugRouteReady().then(function (ok) {
      if (!ok) return false;
      return startRound({ debugRoundMode: 'ai' });
    });
  }

  function startDebugUserRound(keepMode) {
    if (!keepMode) state.debugRoundMode = 'user';
    return ensureDebugRouteReady().then(function (ok) {
      if (!ok) return false;
      var flowToken = resetRoundStartState();
      return post(ROUND_API + '/round/start', roundPayload({ debug_start_phase: 'word_picking' }), 10000)
        .then(function (res) {
          ensureCurrentRoundFlow(flowToken);
          if (!res || !res.ok) throw new Error((res && res.reason) || 'round_start_failed');
          prepareUserDrawing(res.user_draw_options || res.user_draw_answer, res.draw_seconds || ROUND_FALLBACK_SECONDS);
          return true;
        })
        .catch(function (err) {
          if (err && err.staleRoundFlow) return false;
          setPhase('loading_round');
          showPlaceholder();
          addMessage('drawingGuess.messages.roundFailed', 'Round failed: {{reason}}', { reason: readableRequestError(err) });
          return false;
        })
        .finally(updateControls);
    });
  }

  function startNextRound() {
    if (!state.debugRotateRounds && state.debugRoundMode === 'user') return startDebugUserRound(true);
    if (!state.debugRotateRounds && state.debugRoundMode === 'ai') return startDebugAiRound(true);
    return startRound();
  }

  function triggerDebugAiGuessNow() {
    if (!isDebugAiGuessAvailable()) return;
    if (state.phase === 'user_drawing') {
      submitDrawing(false);
      return;
    }
    triggerSupplementGuess(false);
  }

  function addMessage(key, fallback, params, className) {
    var node = document.createElement('div');
    node.className = 'dg-message ' + (className || 'dg-message-system');
    node.textContent = key ? t(key, fallback, params || {}) : String(fallback || '');
    els.messageLog.appendChild(node);
    els.messageLog.scrollTop = els.messageLog.scrollHeight;
    return node;
  }

  function speechAudioSocketUrl() {
    var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return protocol + '//' + window.location.host + ROUTE_API + '/speech/ws'
      + '?lanlan_name=' + encodeURIComponent(state.lanlanName || '')
      + '&session_id=' + encodeURIComponent(state.sessionId || '');
  }

  function pushSpeechAudioHeader(response) {
    var appState = window.appState;
    if (!appState || !Array.isArray(appState.pendingAudioChunkMetaQueue)) return;
    var speechId = String((response && (response.speech_id || response.speechId)) || '').trim();
    if (!speechId) return;
    // suppress_primary_audio 时本 tap 是音频分片唯一的 header 入口，必须与
    // app-websocket.js 的 audio_chunk 处理保持一致：被打断 speech 的迟到分片
    // 要跳过，新 speech 要消费挂起的解码器重置
    var shouldSkip = false;
    if (appState.interruptedSpeechId && speechId === appState.interruptedSpeechId) {
      shouldSkip = true;
    } else if (speechId !== appState.currentPlayingSpeechId) {
      if (appState.pendingDecoderReset) {
        appState.decoderResetPromise = Promise.resolve(
          typeof window.resetOggOpusDecoder === 'function' ? window.resetOggOpusDecoder() : null
        ).then(function () {
          appState.pendingDecoderReset = false;
        });
      } else {
        appState.pendingDecoderReset = false;
      }
      appState.currentPlayingSpeechId = speechId;
      appState.interruptedSpeechId = null;
    }
    appState.pendingAudioChunkMetaQueue.push({
      speechId: speechId,
      turnId: String((response && (response.turn_id || response.turnId)) || speechId),
      shouldSkip: shouldSkip,
      epoch: appState.incomingAudioEpoch || 0,
      receivedAt: Date.now()
    });
    if (window.appAudioPlayback &&
        typeof window.appAudioPlayback.schedulePendingAudioMetaStallCheck === 'function') {
      window.appAudioPlayback.schedulePendingAudioMetaStallCheck();
    } else if (typeof window.schedulePendingAudioMetaStallCheck === 'function') {
      window.schedulePendingAudioMetaStallCheck();
    }
  }

  function currentLive2DSlotModel() {
    var manager = state.live2dManager;
    if (!manager) return null;
    try {
      if (typeof manager.getCurrentModel === 'function') return manager.getCurrentModel();
    } catch (_) {}
    return manager.currentModel || null;
  }

  function currentSpeechAnalyser() {
    var appState = window.appState || null;
    return (appState && appState.globalAnalyser) || window.globalAnalyser || null;
  }

  function live2DModelHasParameter(coreModel, parameterId) {
    if (!coreModel || !parameterId) return false;
    try {
      if (typeof coreModel.getParameterIndex === 'function') {
        return Number(coreModel.getParameterIndex(parameterId)) >= 0;
      }
    } catch (_) {}
    try {
      if (typeof coreModel.getParameterCount === 'function' && typeof coreModel.getParameterId === 'function') {
        var count = Number(coreModel.getParameterCount()) || 0;
        for (var i = 0; i < count; i += 1) {
          if (coreModel.getParameterId(i) === parameterId) return true;
        }
        return false;
      }
    } catch (_) {}
    return true;
  }

  function resolveLive2DMouthParameterId(coreModel) {
    if (state.live2dMouthParameterId && live2DModelHasParameter(coreModel, state.live2dMouthParameterId)) {
      return state.live2dMouthParameterId;
    }
    var ids = ['ParamMouthOpenY', 'ParamMouthOpen', 'ParamA', 'ParamO'];
    for (var i = 0; i < ids.length; i += 1) {
      if (live2DModelHasParameter(coreModel, ids[i])) {
        state.live2dMouthParameterId = ids[i];
        return ids[i];
      }
    }
    state.live2dMouthParameterId = '';
    return '';
  }

  function setLive2DSlotMouth(value) {
    var model = currentLive2DSlotModel();
    var coreModel = model && model.internalModel && model.internalModel.coreModel;
    if (!coreModel || typeof coreModel.setParameterValueById !== 'function') return false;
    var mouthValue = Math.max(0, Math.min(1, Number(value) || 0));
    var parameterId = resolveLive2DMouthParameterId(coreModel);
    if (!parameterId) return false;
    try {
      coreModel.setParameterValueById(parameterId, mouthValue);
      return true;
    } catch (_) {}
    return false;
  }

  function stopDrawingGuessLipSync() {
    clearTimeout(state.lipSyncRetryTimer);
    clearTimeout(state.lipSyncStopTimer);
    state.lipSyncRetryTimer = null;
    state.lipSyncStopTimer = null;
    state.lipSyncRetryDeadline = 0;
    if (state.lipSyncDriver === 'vrm' && state.vrmManager && state.vrmManager.animation &&
        typeof state.vrmManager.animation.stopLipSync === 'function') {
      try { state.vrmManager.animation.stopLipSync(); } catch (_) {}
    }
    if (state.lipSyncDriver === 'mmd' && state.mmdManager && state.mmdManager.animationModule &&
        typeof state.mmdManager.animationModule.stopLipSync === 'function') {
      try { state.mmdManager.animationModule.stopLipSync(); } catch (_) {}
    }
    if (state.lipSyncDriver === 'pngtuber' && state.pngtuberManager &&
        typeof state.pngtuberManager.setSpeaking === 'function') {
      try { state.pngtuberManager.setSpeaking(false); } catch (_) {}
    }
    if (state.lipSyncFrame) {
      cancelAnimationFrame(state.lipSyncFrame);
      state.lipSyncFrame = null;
    }
    state.lipSyncActive = false;
    state.lipSyncAnalyser = null;
    state.lipSyncDriver = '';
    state.lipSyncMouthOpen = 0;
    setLive2DSlotMouth(0);
  }

  function startDrawingGuessLipSync() {
    var analyser = currentSpeechAnalyser();
    var kind = state.modelKind;
    if (state.routeEnding || !state.routeActive) return false;
    if (state.lipSyncActive && state.lipSyncAnalyser === analyser && state.lipSyncDriver === kind) return true;
    if (kind === 'vrm') {
      if (!analyser || !state.vrmManager || !state.vrmManager.currentModel || !state.vrmManager.animation ||
          typeof state.vrmManager.animation.startLipSync !== 'function') return false;
      if (state.lipSyncActive) stopDrawingGuessLipSync();
      state.vrmManager.animation.startLipSync(analyser);
      state.lipSyncActive = true;
      state.lipSyncAnalyser = analyser;
      state.lipSyncDriver = 'vrm';
      return true;
    }
    if (kind === 'mmd') {
      if (!analyser || !state.mmdManager || !state.mmdManager.currentModel || !state.mmdManager.animationModule ||
          typeof state.mmdManager.animationModule.startLipSync !== 'function') return false;
      if (state.lipSyncActive) stopDrawingGuessLipSync();
      state.mmdManager.animationModule.startLipSync(analyser);
      state.lipSyncActive = true;
      state.lipSyncAnalyser = analyser;
      state.lipSyncDriver = 'mmd';
      return true;
    }
    if (kind === 'pngtuber') {
      if (!state.pngtuberManager || typeof state.pngtuberManager.setSpeaking !== 'function') return false;
      if (state.lipSyncActive) stopDrawingGuessLipSync();
      state.pngtuberManager.setSpeaking(true);
      state.lipSyncActive = true;
      state.lipSyncAnalyser = analyser || null;
      state.lipSyncDriver = 'pngtuber';
      return true;
    }
    if (kind !== 'live2d' || !analyser || !currentLive2DSlotModel()) return false;
    if (state.lipSyncActive) stopDrawingGuessLipSync();
    if (state.lipSyncFrame) {
      cancelAnimationFrame(state.lipSyncFrame);
      state.lipSyncFrame = null;
    }
    var dataArray = new Uint8Array(analyser.fftSize || analyser.frequencyBinCount || 2048);
    state.lipSyncActive = true;
    state.lipSyncAnalyser = analyser;
    state.lipSyncDriver = 'live2d';
    state.lipSyncMouthOpen = 0;

    function animate() {
      if (!state.lipSyncActive || state.lipSyncAnalyser !== analyser) return;
      if (state.modelKind !== 'live2d' || !currentLive2DSlotModel()) {
        stopDrawingGuessLipSync();
        return;
      }
      try {
        analyser.getByteTimeDomainData(dataArray);
      } catch (_) {
        stopDrawingGuessLipSync();
        return;
      }
      var sum = 0;
      for (var i = 0; i < dataArray.length; i += 1) {
        var sample = (dataArray[i] - 128) / 128;
        sum += sample * sample;
      }
      var target = Math.min(1, Math.sqrt(sum / Math.max(1, dataArray.length)) * 10);
      state.lipSyncMouthOpen = state.lipSyncMouthOpen * 0.55 + target * 0.45;
      setLive2DSlotMouth(state.lipSyncMouthOpen);
      state.lipSyncFrame = requestAnimationFrame(animate);
    }

    animate();
    return true;
  }

  function scheduleDrawingGuessLipSyncStart() {
    clearTimeout(state.lipSyncRetryTimer);
    state.lipSyncRetryTimer = null;
    if (state.routeEnding || !state.routeActive) return;
    if (startDrawingGuessLipSync()) return;
    state.lipSyncRetryDeadline = Date.now() + 5000;
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

  function handleSpeechPlaybackState(event) {
    var detail = (event && event.detail) || {};
    if (isSpeechPlaybackAudible(detail)) {
      scheduleDrawingGuessLipSyncStart();
      armDrawingGuessLipSyncStop(detail);
      return;
    }
    stopDrawingGuessLipSync();
  }

  function handleSpeechAudioSocketMessage(event) {
    if (event && event.data instanceof Blob) {
      if (typeof window.enqueueIncomingAudioBlob === 'function') {
        window.enqueueIncomingAudioBlob(event.data);
      }
      return;
    }
    var response = null;
    try {
      response = JSON.parse(String((event && event.data) || '{}'));
    } catch (_) {
      return;
    }
    if (response && response.type === 'speech_tap_ready') {
      state.speechAudioTapReady = !!response.ok;
      if (state.speechAudioTapReady) flushNekoVoiceQueue();
      return;
    }
    if (response && response.type === 'audio_chunk') {
      pushSpeechAudioHeader(response);
    }
  }

  function dispatchSpeechAudioTurnEnd(speechId) {
    var normalized = String(speechId || '').trim();
    if (!normalized || typeof window.CustomEvent !== 'function') return;
    window.dispatchEvent(new CustomEvent('neko-assistant-turn-end', {
      detail: {
        turnId: normalized,
        source: 'drawing_guess_speak'
      }
    }));
  }

  function clearSpeechAudioTimers() {
    if (state.speechAudioReconnectTimer) {
      clearTimeout(state.speechAudioReconnectTimer);
      state.speechAudioReconnectTimer = null;
    }
    if (state.speechAudioPingTimer) {
      clearInterval(state.speechAudioPingTimer);
      state.speechAudioPingTimer = null;
    }
  }

  function scheduleSpeechAudioReconnect() {
    if (state.speechAudioReconnectTimer || state.speechAudioManualClose) return;
    if (!state.routeActive || state.routeEnding || !state.lanlanName || !state.sessionId) return;
    state.speechAudioReconnectTimer = setTimeout(function () {
      state.speechAudioReconnectTimer = null;
      startSpeechAudioSocket();
    }, 400);
  }

  function startSpeechAudioSocket() {
    if (!window.WebSocket || !state.routeActive || state.routeEnding || !state.lanlanName || !state.sessionId) return;
    var existing = state.speechAudioSocket;
    if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) return;
    clearSpeechAudioTimers();
    state.speechAudioManualClose = false;
    state.speechAudioTapReady = false;
    var socket = new WebSocket(speechAudioSocketUrl());
    socket.binaryType = 'blob';
    state.speechAudioSocket = socket;
    socket.onopen = function () {
      if (state.speechAudioSocket !== socket) return;
      state.speechAudioPingTimer = setInterval(function () {
        try {
          if (state.speechAudioSocket === socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping', session_id: state.sessionId }));
          }
        } catch (_) {}
      }, 15000);
    };
    socket.onmessage = handleSpeechAudioSocketMessage;
    socket.onclose = function () {
      if (state.speechAudioSocket === socket) {
        state.speechAudioSocket = null;
      }
      state.speechAudioTapReady = false;
      clearSpeechAudioTimers();
      scheduleSpeechAudioReconnect();
    };
    socket.onerror = function () {};
  }

  function stopSpeechAudioSocket() {
    state.speechAudioManualClose = true;
    state.speechAudioTapReady = false;
    clearSpeechAudioTimers();
    var socket = state.speechAudioSocket;
    state.speechAudioSocket = null;
    if (socket) {
      try { socket.close(1000, 'drawing_guess_closed'); } catch (_) {}
    }
  }

  function clearNekoVoiceQueue() {
    state.nekoVoiceQueue = [];
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
    state.nekoVoiceInFlight = true;
    post(ROUTE_API + '/speak', routePayload({
      line: item.line,
      // 与 soccer/badminton 的 speak 一致：机器生成输出必须用后端
      // closed-route guard 认识的 source，route 关闭后的迟到请求才会被跳过
      source: 'game-llm-result',
      request_id: item.requestId,
      mirror_text: false,
      emit_turn_end: true,
      interrupt_audio: false,
      suppress_primary_audio: state.speechAudioTapReady,
      event: {
        kind: 'drawing_guess_neko_line',
        source: 'drawing_guess',
        phase: state.phase,
        round: state.roundNumber,
        text_length: item.line.length
      }
    }), 7000).catch(function (error) {
      console.warn('[DrawingGuessVoice] project TTS unavailable:', error && error.message ? error.message : error);
    }).then(function (res) {
      if (res && res.speech_id && res.turn_end_emitted) {
        dispatchSpeechAudioTurnEnd(res.speech_id);
      }
    }).finally(function () {
      state.nekoVoiceInFlight = false;
      if (state.nekoVoiceQueue.length) {
        setTimeout(flushNekoVoiceQueue, 120);
      }
    });
  }

  function enqueueNekoVoice(text) {
    var line = String(text || '').replace(/\s+/g, ' ').trim();
    if (!line || !state.lanlanName || state.routeEnding) return;
    state.nekoVoiceQueue.push({
      line: line,
      requestId: makeRequestId('drawing-guess-voice')
    });
    if (state.nekoVoiceQueue.length > 5) {
      state.nekoVoiceQueue.splice(0, state.nekoVoiceQueue.length - 5);
    }
    flushNekoVoiceQueue();
  }

  function addNekoMessage(text) {
    var normalized = String(text || '').replace(/\s+/g, ' ').trim();
    if (!normalized) return;
    var recent = state.recentNekoMessages || [];
    if (recent.slice(-4).indexOf(normalized) >= 0) return;
    recent.push(normalized);
    state.recentNekoMessages = recent.slice(-8);
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

  function stopAiGuessSchedule() {
    clearTimeout(state.aiGuessTimer);
    state.aiGuessTimer = null;
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

  function post(url, payload, timeoutMs) {
    var controller = new AbortController();
    var didTimeout = false;
    var timer = setTimeout(function () {
      didTimeout = true;
      controller.abort();
    }, timeoutMs || 10000);
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
      signal: controller.signal
    }).then(function (res) {
      return res.json().catch(function () { return { ok: res.ok }; });
    }).then(function (data) {
      if (data && data.state) applyRoundState(data.state);
      return data;
    }).catch(function (err) {
      if (didTimeout || (err && err.name === 'AbortError')) {
        var timeoutError = new Error('request_timeout');
        timeoutError.code = 'request_timeout';
        throw timeoutError;
      }
      throw err;
    }).finally(function () {
      clearTimeout(timer);
    });
  }

  function readableRequestError(err) {
    if (err && (err.code === 'request_timeout' || err.message === 'request_timeout')) {
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

  function canvasContextPayload(force) {
    if (!shouldShareCanvasContext()) {
      if (state.canvasContextLastHash) {
        state.canvasContextLastHash = '';
        state.canvasContextLastSentAt = 0;
        return { canvas_context_clear: true };
      }
      return {};
    }
    var dataUrl = captureUserCanvasPng();
    if (!dataUrl || dataUrl.length > 1800000) {
      // 当前画布导出失败/超限时不能留着服务端旧快照，否则外部语音的
      // 视觉猜测会拿到过期画面
      if (state.canvasContextLastHash) {
        state.canvasContextLastHash = '';
        state.canvasContextLastSentAt = 0;
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
    return { canvas_image_data_url: dataUrl };
  }

  function routePayload(extra) {
    return Object.assign({
      session_id: state.sessionId,
      lanlan_name: state.lanlanName,
      window_lanlan_name: state.windowLanlanName || state.lanlanName,
      source: 'drawing_guess',
      game_type: GAME_TYPE,
      i18n_language: currentLanguage(),
      gameStarted: state.phase !== 'tutorial',
      game_started: state.phase !== 'tutorial',
      memory_consent: state.memoryConsent,
      client_round_token: state.roundFlowToken,
      currentState: {
        game: GAME_TYPE,
        phase: state.phase,
        memory_consent: state.memoryConsent,
        i18n_language: currentLanguage(),
        client_round_token: state.roundFlowToken,
        round: state.roundNumber,
        has_user_canvas: !!state.hasDrawn,
        canvas_context_visible: shouldShareCanvasContext()
      }
    }, extra || {});
  }

  function pushCanvasContextForRoute(force) {
    if (!state.routeActive || state.routeEnding) return Promise.resolve(false);
    var payload = canvasContextPayload(!!force);
    if (!payload.canvas_image_data_url && !payload.canvas_context_clear) return Promise.resolve(false);
    var visible = !document.hidden;
    return post(ROUTE_API + '/route/heartbeat', routePayload(Object.assign({
      visible: visible,
      pageVisible: visible,
      visibilityState: document.visibilityState || (visible ? 'visible' : 'hidden')
    }, payload)), 5000).then(function (res) {
      return !!(res && res.ok !== false);
    }).catch(function () {
      return false;
    });
  }

  function publishFinalSummaryRouteState() {
    if (!state.routeActive || state.routeEnding) return Promise.resolve(false);
    var visible = !document.hidden;
    return post(ROUTE_API + '/route/heartbeat', routePayload({
      visible: visible,
      pageVisible: visible,
      visibilityState: document.visibilityState || (visible ? 'visible' : 'hidden')
    }), 5000).then(function (res) {
      return !!(res && res.ok !== false);
    }).catch(function () {
      return false;
    });
  }

  function roundPayload(extra) {
    return Object.assign({
      session_id: state.sessionId,
      lanlan_name: state.lanlanName,
      i18n_language: currentLanguage(),
      memory_consent: state.memoryConsent,
      client_round_token: state.activeRoundToken != null ? state.activeRoundToken : state.roundFlowToken
    }, extra || {});
  }

  function hideAllStageViews() {
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
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
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
    var viewBoxRatio = 240 / 180;
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

  function resultLine(res) {
    return String((res && (res.message || res.line || res.evaluation)) || '').trim();
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

  function applyExternalDrawingResult(res) {
    if (!res || !res.ok) return;
    if ((res.kind === 'guess' && res.correct) || res.kind === 'give_up') {
      stopCountdown();
      state.aiAnswerLabel = res.answer ? String(res.answer.label || '') : '';
      addEventMessage('drawingGuess.messages.answerReveal', 'Answer: {{answer}}', {
        answer: res.answer ? res.answer.label : ''
      });
      continueAfterAiDrawingHalf(res, state.roundFlowToken);
      return;
    }
    if (res.kind === 'ai_guess') {
      var guessLabel = res.guess ? res.guess.label : '';
      stopThinkingEventMessage();
      state.pendingAutoGuess = false;
      state.aiGuessAttempts = Number(res.attempt || state.aiGuessAttempts || 0);
      state.maxAiGuessAttempts = Number(res.max_attempts || state.maxAiGuessAttempts || 3);
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
    if (res.state && res.state.phase === 'summary' && (res.summary || res.evaluation || res.answer || res.ai_answer)) {
      renderSummary(res);
    }
  }

  function externalInputText(output) {
    var event = (output && output.event) || {};
    return String(event.userVoiceText || event.userText || event.textRaw || (output.meta && output.meta.inputText) || '').trim();
  }

  function routeOutputMatchesCurrentRound(output) {
    var result = (output && output.result) || {};
    var eventState = (output && output.event && output.event.currentState) || {};
    var resultToken = result.state && result.state.client_round_token;
    var token = state.phase === 'final_summary' && result.kind === 'chat'
      ? resultToken
      : eventState.client_round_token;
    if (token == null) token = resultToken;
    if (token == null) return true;
    return String(token) === String(state.activeRoundToken);
  }
  function handleRouteDrainOutput(output) {
    if (!output || !output.type) return;
    if (output.type === 'game_voice_stt_gate') {
      state.voiceRouteActive = true;
      if (!state.voiceRouteStatusNotified) {
        state.voiceRouteStatusNotified = true;
        addEventMessage('drawingGuess.voice.connectedNotice', 'Voice chat is connected to this round.');
      }
      syncVoiceRouteButton();
      return;
    }
    if (output.type === 'game_canvas_context_request') {
      pushCanvasContextForRoute(true);
      return;
    }
    if (output.type === 'game_external_input') {
      var inputText = externalInputText(output);
      var requestId = String(output.request_id || '');
      // 后端允许无 request_id 的外部输入（按文本去重）；空 id 恒等于初始
      // 空串会把这类转写全部吞掉，退化用文本作去重键
      var externalInputKey = requestId || ('text:' + inputText);
      if (inputText && externalInputKey !== state.lastExternalInputRequestId) {
        state.lastExternalInputRequestId = externalInputKey;
        addUserMessage(inputText);
      }
      return;
    }
    if (output.type !== 'game_llm_result') return;
    var result = output.result || {};
    if (state.routeEnding) return;
    if (state.phase === 'final_summary' && result.kind !== 'chat') return;
    if (!routeOutputMatchesCurrentRound(output)) return;
    var line = resultLine(result);
    if (line) addNekoMessage(line);
    applyExternalDrawingResult(result);
    updateControls();
  }

  function pollRouteDrain() {
    if (!state.routeActive || state.routeEnding || state.routeDrainInFlight) return;
    state.routeDrainInFlight = true;
    var visible = !document.hidden;
    var payload = Object.assign({
      visible: visible,
      pageVisible: visible,
      visibilityState: document.visibilityState || (visible ? 'visible' : 'hidden')
    }, canvasContextPayload(false));
    post(ROUTE_API + '/route/drain', routePayload(payload), 6000).then(function (res) {
      if (!res || !res.ok) return;
      if (res.state && res.state.game_route_active === false) {
        state.routeActive = false;
        state.voiceRouteActive = false;
        stopRouteDrain();
        updateControls();
        return;
      }
      (Array.isArray(res.outputs) ? res.outputs : []).forEach(handleRouteDrainOutput);
    }).catch(function () {}).finally(function () {
      state.routeDrainInFlight = false;
    });
  }

  function startRouteDrain() {
    stopRouteDrain();
    pollRouteDrain();
    state.routeDrainTimer = setInterval(pollRouteDrain, 900);
  }

  function stopRouteDrain() {
    clearInterval(state.routeDrainTimer);
    state.routeDrainTimer = null;
    state.routeDrainInFlight = false;
  }

  function startHeartbeat() {
    clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = setInterval(function () {
      if (!state.routeActive) return;
      var visible = !document.hidden;
      var heartbeatPayload = Object.assign({
        visible: visible,
        pageVisible: visible,
        visibilityState: document.visibilityState || (visible ? 'visible' : 'hidden')
      }, canvasContextPayload(false));
      post(ROUTE_API + '/route/heartbeat', routePayload(heartbeatPayload), 5000).then(function (res) {
        if (res && res.active === false) {
          state.routeActive = false;
          clearInterval(state.heartbeatTimer);
          stopRouteDrain();
          state.voiceRouteActive = false;
          setStatus('heartbeatLost', 'Route inactive');
          updateControls();
        }
      }).catch(function () {});
    }, 2500);
  }

  function startRoute() {
    if (!state.lanlanName) {
      setStatus('missingCharacter', 'Missing character');
      addMessage('drawingGuess.messages.missingCharacter', 'Open this page with a character or load the current one first.');
      updateControls();
      return Promise.resolve(false);
    }
    if (state.routeActive) return Promise.resolve(true);
    state.routeEnding = false;
    setStatus('starting', 'Starting');
    updateControls();
    return post(ROUTE_API + '/route/start', routePayload(), 12000).then(function (res) {
      if (!res || !res.ok) {
        setStatus('failed', 'Start failed');
        addMessage('drawingGuess.messages.startFailed', 'Route start failed: {{reason}}', { reason: (res && res.reason) || 'unknown' });
        return false;
      }
      state.routeActive = true;
      state.voiceRouteActive = false;
      state.voiceRouteStatusNotified = false;
      if (res.state && res.state.lanlan_name) state.lanlanName = String(res.state.lanlan_name || state.lanlanName);
      setStatus('active', 'Active');
      startSpeechAudioSocket();
      startHeartbeat();
      startRouteDrain();
      return true;
    }).catch(function () {
      setStatus('failed', 'Start failed');
      addMessage('drawingGuess.messages.startFailed', 'Route start failed: {{reason}}', { reason: 'request_failed' });
      return false;
    }).finally(function () {
      stopThinkingEventMessage();
      updateControls();
    });
  }

  function endRoute(useBeacon, options) {
    options = options || {};
    var completedRoute = !!options.finalSummary || state.phase === 'summary' || state.phase === 'final_summary';
    clearNekoVoiceQueue();
    stopSpeechAudioSocket();
    stopRouteDrain();
    state.voiceRouteActive = false;
    state.voiceRouteStatusNotified = false;
    state.lastExternalInputRequestId = '';
    state.canvasContextLastHash = '';
    state.canvasContextLastSentAt = 0;
    stopCountdown();
    stopDrawPickAnimation();
    stopAiGuessSchedule();
    if (!state.routeActive && !useBeacon) return Promise.resolve({ ok: true });
    state.routeEnding = true;
    setStatus('ending', 'Ending');
    updateControls();
    clearInterval(state.heartbeatTimer);
    var payload = JSON.stringify(routePayload({
      reason: completedRoute ? 'drawing_guess_game_over' : 'drawing_guess_abandoned',
      roundCompleted: completedRoute,
      round_completed: completedRoute,
      postgameProactive: false
    }));
    if (useBeacon && navigator.sendBeacon) {
      try {
        if (navigator.sendBeacon(ROUTE_API + '/route/end', new Blob([payload], { type: 'application/json' }))) {
          return Promise.resolve({ ok: true, beacon: true });
        }
      } catch (_) {}
    }
    return fetch(ROUTE_API + '/route/end', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload
    }).then(function (res) {
      return res.json().catch(function () { return { ok: res.ok }; });
    }).catch(function () {
      return { ok: false, reason: 'request_failed' };
    }).finally(function () {
      state.routeActive = false;
      state.routeEnding = false;
      setStatus('ended', 'Ended');
      if (options.finalSummary) {
        renderFinalSummary();
      } else {
        setPhase('ended');
      }
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
    updateDebugGuessCountdown();
  }

  function triggerRandomAiGuess(imageDataUrl) {
    if (state.phase !== 'ai_guess_feedback') return;
    var snapshot = imageDataUrl || captureUserCanvasPng();
    if (snapshot) state.userPng = snapshot;
    if (state.chatInFlight || state.aiGuessInFlight) {
      state.pendingAutoGuess = true;
      state.pendingAutoGuessImage = snapshot || state.userPng || '';
      return;
    }
    state.pendingAutoGuess = false;
    state.pendingAutoGuessImage = '';
    startThinkingEventMessage('drawingGuess.messages.aiGuessing', 'She is looking at your drawing...');
    postVisionGuess('', { auto: true, image_data_url: snapshot || state.userPng });
  }

  function handleAiGuessTimeout() {
    stopAiGuessSchedule();
    if (state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback') return;
    if (state.chatInFlight || state.aiGuessInFlight) {
      state.pendingAiGuessTimeout = true;
      return;
    }
    settleAiGuessTimeout();
  }

  function settleAiGuessTimeout(attempt) {
    stopAiGuessSchedule();
    if (state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback') return;
    attempt = Number(attempt || 0);
    var flowToken = state.roundFlowToken;
    return post(ROUND_API + '/timeout', roundPayload(), 10000).then(function (res) {
      if (!isCurrentRoundFlow(flowToken)) return;
      if (!res || !res.ok) {
        if (res && res.reason === 'session_busy') {
          state.pendingAiGuessTimeout = true;
          clearTimeout(state.aiGuessTimeoutRetryTimer);
          var busyPollCount = 0;
          var retryWhenReady = function () {
            state.aiGuessTimeoutRetryTimer = null;
            if (!isCurrentRoundFlow(flowToken)) return;
            if (state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback') return;
            if (state.chatInFlight || state.aiGuessInFlight) {
              busyPollCount += 1;
              if (busyPollCount >= AI_GUESS_TIMEOUT_BUSY_MAX_POLLS) {
                state.pendingAiGuessTimeout = false;
                addMessage('drawingGuess.messages.roundFailed', 'Round failed: {{reason}}', { reason: 'session_busy' });
                updateControls();
                return;
              }
              state.aiGuessTimeoutRetryTimer = setTimeout(retryWhenReady, 120);
              return;
            }
            state.pendingAiGuessTimeout = false;
            settleAiGuessTimeout(attempt);
          };
          state.aiGuessTimeoutRetryTimer = setTimeout(retryWhenReady, 180);
        }
        return;
      }
      state.pendingAiGuessTimeout = false;
      if (res.message) addNekoMessage(res.message);
      if (res.phase === 'summary' || (res.state && res.state.phase === 'summary')) {
        renderSummary(res);
      }
    }).catch(function (err) {
      if (!isCurrentRoundFlow(flowToken)) return;
      clearTimeout(state.aiGuessTimeoutRetryTimer);
      if (attempt < AI_GUESS_TIMEOUT_MAX_RETRIES) {
        state.aiGuessTimeoutRetryTimer = setTimeout(function () {
          if (!isCurrentRoundFlow(flowToken)) return;
          settleAiGuessTimeout(attempt + 1);
        }, 500 * Math.pow(2, attempt));
        return;
      }
      state.pendingAiGuessTimeout = false;
      addMessage('drawingGuess.messages.roundFailed', 'Round failed: {{reason}}', {
        reason: readableRequestError(err)
      });
    }).finally(updateControls);
  }

  function flushDeferredAiGuessWork() {
    if (state.aiGuessInFlight || state.chatInFlight) return;
    if (state.pendingSupplementGuess) {
      var supplementImage = state.pendingSupplementImage;
      state.pendingSupplementGuess = false;
      state.pendingSupplementImage = '';
      triggerSupplementGuess(false, supplementImage);
      return;
    }
    if (state.pendingAiGuessTimeout) {
      state.pendingAiGuessTimeout = false;
      settleAiGuessTimeout();
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
    // 才发请求），避免下一次 drain 心跳前的外部语音把旧画布喂给视觉猜测
    pushCanvasContextForRoute(true);
    state.roundNumber += 1;
    state.currentRoundSummarySaved = false;
    state.aiSvg = '';
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

  function startRound(options) {
    options = options || {};
    if (options.debugRoundMode) state.debugRoundMode = options.debugRoundMode;
    var flowToken = resetRoundStartState();
    return post(ROUND_API + '/round/start', roundPayload(), 10000)
      .then(function (res) {
        ensureCurrentRoundFlow(flowToken);
        if (!res || !res.ok) throw new Error((res && res.reason) || 'round_start_failed');
        setPhase('ai_drawing');
        setBadge(t('drawingGuess.phases.ai_drawing', 'Neko drawing'));
        scheduleAiDrawingPlaceholderHint();
        return post(ROUND_API + '/ai-draw', roundPayload(), AI_DRAW_REQUEST_TIMEOUT_MS);
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
        state.aiSvg = (res.drawing && res.drawing.svg) || '';
        showAiDrawing(state.aiSvg);
        setPhase('user_guessing');
        setChatPlaceholder('drawingGuess.input.guessPlaceholder', 'Type your guess or ask for a hint');
        addNekoMessage(res.message || t('drawingGuess.messages.aiDrawingReady', 'She finished drawing. Try to guess it.'));
        startCountdown(res.guess_seconds || ROUND_FALLBACK_SECONDS, handleGuessTimeout);
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

  function shouldStayOnDebugAiRound() {
    return !state.debugRotateRounds && state.debugRoundMode === 'ai';
  }

  function continueAfterAiDrawingHalf(res, flowToken) {
    if (!isCurrentRoundFlow(flowToken)) return;
    if (shouldStayOnDebugAiRound()) {
      stopCountdown();
      addEventMessage(
        'drawingGuess.debug.holdAiRound',
        'Debug: keeping Neko round, preparing the next word.'
      );
      setTimeout(function () {
        if (!isCurrentRoundFlow(flowToken)) return;
        startDebugAiRound(true);
      }, 450);
      return;
    }
    prepareUserDrawing(res.user_draw_options || res.user_draw_answer, res.draw_seconds || ROUND_FALLBACK_SECONDS);
  }

  function requestGuessTimeout(flowToken, attempt) {
    return post(ROUND_API + '/timeout', roundPayload(), 10000).then(function (res) {
      if (!isCurrentRoundFlow(flowToken)) return;
      if (!res || !res.ok) throw new Error((res && res.reason) || 'timeout_failed');
      state.guessTimeoutRetryTimer = null;
      addNekoMessage(res.message || t('drawingGuess.messages.guessTimeout', 'Time is up. The answer was {{answer}}.', {
        answer: res.answer ? res.answer.label : ''
      }));
      state.aiAnswerLabel = res.answer ? String(res.answer.label || '') : '';
      addEventMessage('drawingGuess.messages.answerReveal', 'Answer: {{answer}}', { answer: res.answer ? res.answer.label : '' });
      continueAfterAiDrawingHalf(res, flowToken);
    }).catch(function (err) {
      if (!isCurrentRoundFlow(flowToken)) return;
      if (attempt === 0) {
        addMessage('drawingGuess.messages.roundFailed', 'Round failed: {{reason}}', {
          reason: readableRequestError(err)
        });
      }
      clearTimeout(state.guessTimeoutRetryTimer);
      state.guessTimeoutRetryTimer = setTimeout(function () {
        if (!isCurrentRoundFlow(flowToken)) return;
        requestGuessTimeout(flowToken, attempt + 1);
      }, Math.min(5000, 1000 * Math.pow(2, attempt)));
    });
  }

  function handleGuessTimeout() {
    var flowToken = state.roundFlowToken;
    setPhase('loading_round');
    requestGuessTimeout(flowToken, 0);
  }

  function submitUserGuess(text) {
    var flowToken = state.roundFlowToken;
    return post(ROUND_API + '/input', roundPayload({ text: text }), 10000).then(function (res) {
      if (!isCurrentRoundFlow(flowToken)) return;
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      addNekoMessage(res.message || '');
      if (res.correct || res.kind === 'give_up') {
        stopCountdown();
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
    return post(ROUND_API + '/input', roundPayload({
      text: text,
      summary_chat_only: !!options.summaryChatOnly
    }), 20000).then(function (res) {
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

  function submitFeedbackInput(text) {
    state.chatInFlight = true;
    var flowToken = state.roundFlowToken;
    var feedbackImage = captureUserCanvasPng();
    if (feedbackImage) state.userPng = feedbackImage;
    return post(ROUND_API + '/input', roundPayload({
      text: text,
      image_data_url: feedbackImage || state.userPng
    }), AI_GUESS_REQUEST_TIMEOUT_MS).then(function (res) {
      if (!isCurrentRoundFlow(flowToken)) return;
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      if (res.kind === 'ai_guess') {
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
    return post(ROUND_API + '/choose-word', roundPayload({ word_id: wordId }), 10000).then(function (res) {
      // 与其它异步回合回调一致：End/换轮之后的迟到 choose-word 结果不再落地
      if (!isCurrentRoundFlow(flowToken)) return;
      if (!res || !res.ok) {
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
    if (!isCanvasEditablePhase()) return;
    if (manual && !state.hasDrawn) {
      addNekoMessage(t('drawingGuess.messages.blankCanvas', 'Give her a few lines first.'));
      return;
    }
    if (state.phase !== 'user_drawing') {
      triggerSupplementGuess(true);
      return;
    }
    stopCountdown();
    stopAiGuessSchedule();
    state.userPng = captureUserCanvasPng();
    state.aiGuessDeadline = Date.now() + ROUND_FALLBACK_SECONDS * 1000;
    state.aiGuessAttempts = 0;
    setPhase('ai_guessing');
    setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Keep chatting; give a hint when you want her to guess again');
    startThinkingEventMessage('drawingGuess.messages.aiGuessing', 'She is looking at your drawing...');
    startCountdown(ROUND_FALLBACK_SECONDS, handleAiGuessTimeout);
    postVisionGuess('', { first_guess: true });
  }

  function triggerSupplementGuess(announce, imageDataUrl) {
    if (state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback') return;
    state.userPng = imageDataUrl || captureUserCanvasPng() || state.userPng;
    clearTimeout(state.aiGuessTimer);
    state.aiGuessTimer = null;
    state.pendingAutoGuess = false;
    state.pendingAutoGuessImage = '';
    if (state.aiGuessInFlight || state.chatInFlight) {
      state.pendingSupplementGuess = true;
      state.pendingSupplementImage = state.userPng || '';
      return;
    }
    state.pendingSupplementGuess = false;
    state.pendingSupplementImage = '';
    startThinkingEventMessage('drawingGuess.messages.aiGuessing', 'She is looking at your drawing...');
    postVisionGuess('', { supplement: true, image_data_url: state.userPng });
  }

  function postVisionGuess(userHint, options) {
    state.aiGuessInFlight = true;
    var flowToken = state.roundFlowToken;
    var imageDataUrl = (options && options.image_data_url) || state.userPng;
    return post(ROUND_API + '/vision-guess', roundPayload({
      image_data_url: imageDataUrl,
      user_hint: userHint || '',
      settle_on_miss: !!(options && options.settle_on_miss),
      time_expired: !!(options && options.settle_on_miss)
    }), AI_GUESS_REQUEST_TIMEOUT_MS).then(function (res) {
      if (!isCurrentRoundFlow(flowToken)) return;
      stopThinkingEventMessage();
      if (!res || !res.ok) {
        if (res && res.reason === 'session_busy' && Number((options && options.busy_retry_count) || 0) < 3) {
          var retryOptions = Object.assign({}, options || {}, {
            busy_retry_count: Number((options && options.busy_retry_count) || 0) + 1
          });
          var retryWhenReady = function () {
            if (!isCurrentRoundFlow(flowToken)) return;
            if (state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback') return;
            if (state.aiGuessInFlight) {
              setTimeout(retryWhenReady, 120);
              return;
            }
            postVisionGuess(userHint, retryOptions);
          };
          setTimeout(retryWhenReady, 180);
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
    }).finally(function () {
      if (!isCurrentRoundFlow(flowToken)) return;
      state.aiGuessInFlight = false;
      stopThinkingEventMessage();
      flushDeferredAiGuessWork();
      updateControls();
    });
  }

  function renderSummary(res) {
    stopCountdown();
    stopThinkingEventMessage();
    stopDrawPickAnimation();
    stopAiGuessSchedule();
    persistCurrentUserCanvasSnapshot();
    setPhase('summary');
    showSummary();
    var answerLabel = res.answer ? res.answer.label : (state.userDrawAnswer ? state.userDrawAnswer.label : '');
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

  function cleanConfigValue(value) {
    if (value == null) return '';
    var text = String(value).trim();
    var lower = text.toLowerCase();
    if (!text || lower === 'undefined' || lower === 'null') return '';
    return text;
  }

  function cleanStringConfigValue(value) {
    return typeof value === 'string' ? cleanConfigValue(value) : '';
  }

  function getReservedAvatar(character) {
    if (!character || typeof character !== 'object') return {};
    var reserved = character._reserved && typeof character._reserved === 'object' ? character._reserved : {};
    return reserved.avatar && typeof reserved.avatar === 'object' ? reserved.avatar : {};
  }

  function normalizeAvatarConfig(character) {
    var avatar = getReservedAvatar(character);
    var live2d = avatar.live2d && typeof avatar.live2d === 'object' ? avatar.live2d : {};
    var vrm = avatar.vrm && typeof avatar.vrm === 'object' ? avatar.vrm : {};
    var mmd = avatar.mmd && typeof avatar.mmd === 'object' ? avatar.mmd : {};
    var pngtuberAvatar = avatar.pngtuber && typeof avatar.pngtuber === 'object' ? avatar.pngtuber : {};
    var pngtuberTop = character && character.pngtuber && typeof character.pngtuber === 'object' ? character.pngtuber : {};
    var pngtuberConfig = Object.assign({}, pngtuberTop, pngtuberAvatar);
    var modelType = cleanConfigValue(character && character.model_type) || cleanConfigValue(avatar.model_type) || 'live2d';
    var live3dSubType = cleanConfigValue(character && character.live3d_sub_type) || cleanConfigValue(avatar.live3d_sub_type);
    var modelPath = cleanStringConfigValue(character && character.model_path);
    var vrmPath = cleanStringConfigValue(character && character.vrm) || cleanStringConfigValue(vrm.model_path);
    var mmdPath = cleanStringConfigValue(character && character.mmd) || cleanStringConfigValue(mmd.model_path);
    var live2dPath = cleanStringConfigValue(character && character.live2d) || cleanStringConfigValue(live2d.model_path);
    var pngtuberPath = cleanStringConfigValue(pngtuberConfig.idle_image)
      || cleanStringConfigValue(character && character.pngtuber_idle_image)
      || cleanStringConfigValue(character && character.pngtuber)
      || modelPath;
    var modelTypeLower = modelType.toLowerCase();
    var live3dSubTypeLower = live3dSubType.toLowerCase();
    if (!vrmPath && (modelTypeLower === 'vrm' || (modelTypeLower === 'live3d' && live3dSubTypeLower === 'vrm'))) {
      vrmPath = modelPath;
    }
    if (!mmdPath && (modelTypeLower === 'mmd' || (modelTypeLower === 'live3d' && live3dSubTypeLower === 'mmd'))) {
      mmdPath = modelPath;
    }
    if (!live2dPath && modelTypeLower === 'live2d') {
      live2dPath = modelPath;
    }
    if (!pngtuberConfig.idle_image && pngtuberPath) {
      pngtuberConfig.idle_image = pngtuberPath;
    }
    if (!pngtuberConfig.talking_image) {
      var talkingPath = cleanStringConfigValue(character && character.pngtuber_talking_image);
      if (talkingPath) pngtuberConfig.talking_image = talkingPath;
    }
    var effectiveKind = modelTypeLower;
    if (effectiveKind === 'live3d') {
      if (live3dSubTypeLower === 'mmd') {
        effectiveKind = 'mmd';
      } else if (live3dSubTypeLower === 'vrm' || vrmPath) {
        effectiveKind = 'vrm';
      }
    } else if (!effectiveKind || effectiveKind === 'default') {
      effectiveKind = pngtuberPath ? 'pngtuber' : (vrmPath ? 'vrm' : 'live2d');
    } else if (effectiveKind === 'png' || effectiveKind === 'png-tuber') {
      effectiveKind = 'pngtuber';
    }
    return {
      modelType: modelType,
      live3dSubType: live3dSubType,
      effectiveKind: effectiveKind,
      modelPath: modelPath,
      live2dPath: live2dPath,
      vrmPath: vrmPath,
      mmdPath: mmdPath,
      pngtuberConfig: pngtuberConfig,
      lighting: character && character.lighting,
      vrmIdleAnimation: character && character.idleAnimation,
      vrmIdleAnimations: character && character.idleAnimations
    };
  }

  function syncLanlanAvatarConfig(config) {
    window.lanlan_config = window.lanlan_config || {};
    if (config.effectiveKind === 'vrm' || config.effectiveKind === 'mmd') {
      window.lanlan_config.model_type = 'live3d';
      window.lanlan_config.live3d_sub_type = config.effectiveKind;
    } else if (config.effectiveKind === 'pngtuber') {
      window.lanlan_config.model_type = 'pngtuber';
      window.lanlan_config.live3d_sub_type = '';
    } else {
      window.lanlan_config.model_type = config.modelType || '';
      window.lanlan_config.live3d_sub_type = config.live3dSubType || '';
    }
    if (config.modelPath) window.lanlan_config.model_path = config.modelPath;
    if (config.vrmPath) {
      window.lanlan_config.vrm = config.vrmPath;
      window.vrmModel = config.vrmPath;
    }
    if (config.mmdPath) {
      window.lanlan_config.mmd = config.mmdPath;
      window.mmdModel = config.mmdPath;
    }
    if (config.pngtuberConfig && Object.keys(config.pngtuberConfig).length > 0) {
      window.lanlan_config.pngtuber = Object.assign({}, config.pngtuberConfig);
    }
    if (config.lighting) window.lanlan_config.lighting = config.lighting;
    if (config.vrmIdleAnimation) window.lanlan_config.vrmIdleAnimation = config.vrmIdleAnimation;
    if (Array.isArray(config.vrmIdleAnimations)) window.lanlan_config.vrmIdleAnimations = config.vrmIdleAnimations;
  }

  function fetchCharacterAvatarConfig(name) {
    if (!name) return Promise.resolve(normalizeAvatarConfig(null));
    return fetch('/api/characters', { cache: 'no-store' }).then(function (res) {
      if (!res.ok) throw new Error('characters_fetch_failed_' + res.status);
      return res.json();
    }).then(function (data) {
      var characters = data && data['猫娘'];
      var character = characters && characters[name];
      return normalizeAvatarConfig(character || null);
    }).catch(function () {
      return normalizeAvatarConfig(null);
    });
  }

  function waitForModelRuntime(predicate, readyEvent, failedEvent, timeoutMs, label) {
    if (predicate()) return Promise.resolve(true);
    return new Promise(function (resolve, reject) {
      var settled = false;
      var timer = null;
      function cleanup() {
        if (timer) clearTimeout(timer);
        if (readyEvent) window.removeEventListener(readyEvent, onReady);
        if (failedEvent) window.removeEventListener(failedEvent, onFailed);
      }
      function finish(ok, error) {
        if (settled) return;
        settled = true;
        cleanup();
        if (ok) resolve(true);
        else reject(error || new Error((label || 'model runtime') + '_unavailable'));
      }
      function onReady() {
        if (predicate()) finish(true);
      }
      function onFailed(event) {
        var failed = event && event.detail && event.detail.failedModules;
        finish(false, new Error((label || 'model runtime') + '_failed' + (failed ? ':' + failed.join(',') : '')));
      }
      if (readyEvent) window.addEventListener(readyEvent, onReady);
      if (failedEvent) window.addEventListener(failedEvent, onFailed);
      timer = setTimeout(function () {
        finish(predicate(), new Error((label || 'model runtime') + '_timeout'));
      }, timeoutMs || 10000);
    });
  }

  function cleanupPNGTuberSlotControls() {
    Array.prototype.slice.call(document.querySelectorAll('#pngtuber-floating-buttons, #pngtuber-lock-icon, #pngtuber-return-button-container'))
      .forEach(function (node) {
        if (node && node.parentNode) node.parentNode.removeChild(node);
      });
  }

  function suppressSlotManagerChrome(manager) {
    if (!manager) return;
    manager.setupFloatingButtons = function () {};
    manager.setupHTMLLockIcon = function () {};
  }

  function loadVRMSlot(config) {
    var modelPath = cleanConfigValue(config && config.vrmPath) || '/static/vrm/sister1.0.vrm';
    return waitForModelRuntime(function () {
      return window.vrmModuleLoaded && typeof window.VRMManager === 'function';
    }, 'vrm-modules-ready', 'vrm-modules-failed', 10000, 'vrm_runtime').then(function () {
      var manager = state.vrmManager || window.vrmManager || new window.VRMManager();
      state.vrmManager = manager;
      window.vrmManager = manager;
      suppressSlotManagerChrome(manager);
      showModelLayer('vrm');
      setModelLoadState('loading');
      applyEmbeddedModelSlotStyles('vrm');
      var modelUrl = typeof window.convertVRMModelPath === 'function' ? window.convertVRMModelPath(modelPath) : modelPath;
      return Promise.resolve(manager.initThreeJS('vrm-canvas', 'vrm-container', config && config.lighting ? config.lighting : null))
        .then(function (ok) {
          if (ok === false) throw new Error('vrm_scene_init_failed');
          suppressSlotManagerChrome(manager);
          applyEmbeddedModelSlotStyles('vrm');
          return manager.loadModel(modelUrl, {
            canvasId: 'vrm-canvas',
            containerId: 'vrm-container'
          });
        })
        .then(function () {
          showModelLayer('vrm');
          setModelLoadState('ready');
          installModelResizeHandler();
          resizeActiveModelRenderer();
          return true;
        });
    });
  }

  function loadMMDSlot(config) {
    var modelPath = cleanConfigValue(config && config.mmdPath) || '/static/mmd/Miku/Miku.pmx';
    return waitForModelRuntime(function () {
      return window.mmdModuleLoaded && typeof window.MMDManager === 'function';
    }, 'mmd-modules-ready', 'mmd-modules-failed', 10000, 'mmd_runtime').then(function () {
      var manager = state.mmdManager || window.mmdManager || new window.MMDManager();
      state.mmdManager = manager;
      window.mmdManager = manager;
      suppressSlotManagerChrome(manager);
      showModelLayer('mmd');
      setModelLoadState('loading');
      applyEmbeddedModelSlotStyles('mmd');
      var fetchPaths = typeof window.fetchMMDConfig === 'function'
        ? Promise.resolve(window.fetchMMDConfig()).catch(function () { return false; })
        : Promise.resolve(false);
      return fetchPaths.then(function () {
        var resolvedPath = typeof window._mmdConvertPath === 'function' ? window._mmdConvertPath(modelPath) : modelPath;
        var initPromise = manager.core && manager.core.renderer
          ? Promise.resolve(true)
          : Promise.resolve(manager.init('mmd-canvas', 'mmd-container'));
        return initPromise.then(function () {
          suppressSlotManagerChrome(manager);
          applyEmbeddedModelSlotStyles('mmd');
          return manager.loadModel(resolvedPath, {});
        });
      }).then(function () {
        showModelLayer('mmd');
        setModelLoadState('ready');
        installModelResizeHandler();
        resizeActiveModelRenderer();
        return true;
      });
    });
  }

  function loadPNGTuberSlot(config) {
    var pngtuberConfig = Object.assign({}, (config && config.pngtuberConfig) || {});
    if (!pngtuberConfig.idle_image && config && config.modelPath) {
      pngtuberConfig.idle_image = config.modelPath;
    }
    if (!pngtuberConfig.idle_image) {
      return Promise.reject(new Error('pngtuber_idle_image_missing'));
    }
    return waitForModelRuntime(function () {
      return typeof window.PNGTuberManager === 'function';
    }, null, null, 10000, 'pngtuber_runtime').then(function () {
      var manager = state.pngtuberManager || new window.PNGTuberManager('pngtuber-container');
      state.pngtuberManager = manager;
      window.pngtuberManager = manager;
      suppressSlotManagerChrome(manager);
      showModelLayer('pngtuber');
      setModelLoadState('loading');
      applyEmbeddedModelSlotStyles('pngtuber');
      return Promise.resolve(manager.load(pngtuberConfig)).then(function () {
        manager.detachSpeechListeners && manager.detachSpeechListeners();
        manager.detachDragListeners && manager.detachDragListeners();
        manager.detachLayeredHotkeys && manager.detachLayeredHotkeys();
        manager.detachLayeredPlayEvent && manager.detachLayeredPlayEvent();
        manager.cleanupFloatingButtons && manager.cleanupFloatingButtons();
        cleanupPNGTuberSlotControls();
        manager.setSpeaking && manager.setSpeaking(false);
        manager.setState && manager.setState('idle');
        manager.show && manager.show();
        manager.clearLayeredTimers && manager.clearLayeredTimers();
        if (manager.isLayeredActive && manager.isLayeredActive() && manager.drawLayeredState) {
          manager.drawLayeredState('idle');
        }
        applyEmbeddedModelSlotStyles('pngtuber');
        showModelLayer('pngtuber');
        setModelLoadState('ready');
        installModelResizeHandler();
        applyPNGTuberMood(state.modelMood);
        return true;
      });
    });
  }

  function ensureLive2DSlotMethods(manager) {
    if (!manager) return;
    manager.setupFloatingButtons = function () {};
    manager.setupHTMLLockIcon = function () {};
    manager.setFullscreenTrackingEnabled = function () {};
    manager.enableMouseTracking = function () {};
    manager.setupDragAndDrop = function (model) {
      if (model) model.interactive = false;
    };
  }

  function fitLive2DModelToSlot() {
    var manager = state.live2dManager;
    if (!manager || !manager.pixi_app || !manager.pixi_app.renderer || !els.modelStage) return;
    var rect = els.modelStage.getBoundingClientRect();
    var stageWidth = Math.max(1, Math.round(rect.width || 1));
    var stageHeight = Math.max(1, Math.round(rect.height || 1));
    if (!state.modelFitBase) {
      state.modelFitBase = {
        width: stageWidth,
        height: stageHeight
      };
    }
    var fitWidth = Math.max(1, Math.min(stageWidth, state.modelFitBase.width));
    var fitHeight = Math.max(1, Math.min(stageHeight, state.modelFitBase.height));
    try {
      manager.pixi_app.renderer.resize(stageWidth, stageHeight);
      var canvas = manager.pixi_app.view || (manager.pixi_app.renderer && manager.pixi_app.renderer.view) || els.live2dCanvas;
      if (canvas && canvas.style) {
        canvas.style.setProperty('width', stageWidth + 'px', 'important');
        canvas.style.setProperty('height', stageHeight + 'px', 'important');
      }
    } catch (_) {}
    var model = typeof manager.getCurrentModel === 'function' ? manager.getCurrentModel() : manager.currentModel;
    if (!model) return;
    try {
      var view = normalizeModelView(state.modelView);
      if (model.anchor && typeof model.anchor.set === 'function') model.anchor.set(0.5, 0.5);
      var localBounds = null;
      try {
        localBounds = typeof model.getLocalBounds === 'function' ? model.getLocalBounds() : null;
      } catch (_) {}
      var rawWidth = localBounds && localBounds.width > 0 ? localBounds.width : 1200;
      var rawHeight = localBounds && localBounds.height > 0 ? localBounds.height : 1800;
      var scale = Math.min(fitWidth * 0.78 / rawWidth, fitHeight * 0.86 / rawHeight) * (view.scale / 100);
      if (!Number.isFinite(scale) || scale <= 0) scale = Math.min(fitWidth, fitHeight) / 1600;
      scale = Math.max(0.025, Math.min(0.68, scale));
      if (model.scale && typeof model.scale.set === 'function') model.scale.set(scale);
      model.x = fitWidth * 0.5;
      model.y = fitHeight * 0.5;
      var bounds = null;
      try {
        bounds = typeof model.getBounds === 'function' ? model.getBounds() : null;
      } catch (_) {
        bounds = null;
      }
      if (bounds && bounds.width > 0 && bounds.height > 0) {
        model.x += fitWidth * 0.5 - (bounds.x + bounds.width / 2);
        model.y += fitHeight * 0.5 - (bounds.y + bounds.height / 2);
      }
      model.x += fitWidth * (view.x / 100);
      model.y += fitHeight * (view.y / 100);
    } catch (err) {
      console.warn('[drawing_guess] Live2D slot fit failed:', err);
    }
  }

  function installModelResizeHandler() {
    if (state.modelResizeHandler) return;
    state.modelResizeHandler = function () {
      resizeActiveModelRenderer();
    };
    window.addEventListener('resize', state.modelResizeHandler);
  }

  function loadLive2DSlot(name) {
    if (typeof window.Live2DManager !== 'function' || !window.PIXI || !window.PIXI.live2d) {
      return Promise.reject(new Error('live2d_runtime_unavailable'));
    }
    var modelConfigUrl = '';
    return fetch('/api/characters/current_live2d_model?catgirl_name=' + encodeURIComponent(name || ''), { cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) throw new Error('live2d_config_fetch_failed_' + res.status);
        return res.json();
      })
      .then(function (data) {
        modelConfigUrl = data && data.success && data.model_info ? cleanConfigValue(data.model_info.path) : '';
        if (!modelConfigUrl) throw new Error((data && data.error) || 'live2d_model_path_missing');
        return fetch(modelConfigUrl, { cache: 'no-store' });
      })
      .then(function (res) {
        if (!res.ok) throw new Error('live2d_model_json_failed_' + res.status);
        return res.json();
      })
      .then(function (modelConfig) {
        modelConfig.url = modelConfigUrl;
        var manager = state.live2dManager || new window.Live2DManager();
        state.live2dManager = manager;
        state.modelFitBase = null;
        state.live2dMouthParameterId = '';
        ensureLive2DSlotMethods(manager);
        if (els.live2dContainer) els.live2dContainer.hidden = false;
        setModelLoadState('loading');
        var initPromise = typeof manager.ensurePIXIReady === 'function'
          ? manager.ensurePIXIReady('live2d-canvas', 'live2d-container', { backgroundAlpha: 0, antialias: true })
          : manager.initPIXI('live2d-canvas', 'live2d-container', { backgroundAlpha: 0, antialias: true });
        return initPromise.then(function () {
          ensureLive2DSlotMethods(manager);
          return manager.loadModel(modelConfig, {
            isMobile: false,
            skipCloseWindows: true,
            suppressPersistentExpressions: true
          });
        }).then(function () {
          if (manager.pixi_app && manager.pixi_app.ticker && !manager.pixi_app.ticker.started) {
            manager.pixi_app.ticker.start();
          }
          installModelResizeHandler();
          requestAnimationFrame(fitLive2DModelToSlot);
          showModelLayer('live2d');
          setModelLoadState('ready');
          applyLive2DMood(state.modelMood);
          return true;
        });
      });
  }

  function showConfiguredFallback(config) {
    showModelLayer('fallback');
    setModelLoadState('fallback');
    if (config && (config.effectiveKind === 'vrm' || config.effectiveKind === 'mmd' || config.effectiveKind === 'pngtuber')) {
      setModelKind('fallback');
    }
  }

  function initModelSlotForCurrentCharacter(name) {
    if (!els.modelStage) return Promise.resolve(false);
    showModelLayer('loading');
    setModelLoadState('loading');
    setModelMood(modelMoodForPhase(state.phase));
    return fetchCharacterAvatarConfig(name).then(function (config) {
      syncLanlanAvatarConfig(config);
      if (config.effectiveKind === 'live2d') {
        return loadLive2DSlot(name).catch(function (err) {
          console.warn('[drawing_guess] Live2D slot fallback:', err && err.message ? err.message : err);
          showConfiguredFallback(config);
          return false;
        });
      }
      if (config.effectiveKind === 'vrm') {
        return loadVRMSlot(config).catch(function (err) {
          console.warn('[drawing_guess] VRM slot fallback:', err && err.message ? err.message : err);
          showConfiguredFallback(config);
          return false;
        });
      }
      if (config.effectiveKind === 'mmd') {
        return loadMMDSlot(config).catch(function (err) {
          console.warn('[drawing_guess] MMD slot fallback:', err && err.message ? err.message : err);
          showConfiguredFallback(config);
          return false;
        });
      }
      if (config.effectiveKind === 'pngtuber') {
        return loadPNGTuberSlot(config).catch(function (err) {
          console.warn('[drawing_guess] PNGTuber slot fallback:', err && err.message ? err.message : err);
          showConfiguredFallback(config);
          return false;
        });
      }
      showConfiguredFallback(config);
      return false;
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

  function loadCurrentCharacter() {
    if (state.lanlanName) {
      setStatus('ready', 'Ready');
      loadModelViewSettings();
      updateControls();
      initModelSlotForCurrentCharacter(state.lanlanName).catch(function () {});
      return Promise.resolve(state.lanlanName);
    }
    setStatus('loadingCharacter', 'Loading character');
    return fetch('/api/characters/current_catgirl', { cache: 'no-store' }).then(function (res) {
      return res.json();
    }).then(function (data) {
      var name = String((data && data.current_catgirl) || '').trim();
      state.lanlanName = name;
      if (name && !state.windowLanlanName) state.windowLanlanName = name;
      if (name) {
        window.lanlan_config = window.lanlan_config || {};
        window.lanlan_config.lanlan_name = name;
        setStatus('ready', 'Ready');
      } else {
        setStatus('missingCharacter', 'Missing character');
      }
      loadModelViewSettings();
      updateControls();
      if (!name) return name;
      return initModelSlotForCurrentCharacter(name).then(function () {
        return name;
      }).catch(function () {
        return name;
      });
    }).catch(function () {
      setStatus('missingCharacter', 'Missing character');
      updateControls();
      return '';
    });
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

  function colorHistoryStorageKey() {
    return 'drawingGuess.colorHistory';
  }

  function loadColorHistory() {
    var loaded = [];
    try {
      loaded = JSON.parse(localStorage.getItem(colorHistoryStorageKey()) || '[]');
    } catch (_) {
      loaded = [];
    }
    state.colorHistory = Array.isArray(loaded)
      ? loaded.map(normalizeHexColor).filter(Boolean).slice(0, COLOR_HISTORY_MAX_COUNT)
      : [];
  }

  function saveColorHistory() {
    try {
      localStorage.setItem(colorHistoryStorageKey(), JSON.stringify(state.colorHistory.slice(0, COLOR_HISTORY_MAX_COUNT)));
    } catch (_) {}
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

  function floodFillCanvas(point) {
    var x = Math.max(0, Math.min(els.canvas.width - 1, Math.floor(point.x)));
    var y = Math.max(0, Math.min(els.canvas.height - 1, Math.floor(point.y)));
    var image;
    try {
      image = els.ctx.getImageData(0, 0, els.canvas.width, els.canvas.height);
    } catch (_) {
      return false;
    }
    var data = image.data;
    var width = image.width;
    var height = image.height;
    var startPixel = y * width + x;
    var startIndex = startPixel * 4;
    var target = {
      r: data[startIndex],
      g: data[startIndex + 1],
      b: data[startIndex + 2],
      a: data[startIndex + 3]
    };
    var fill = hexToRgba(currentBrushColor());
    if (pixelMatches(data, startIndex, fill)) return false;
    var stack = [startPixel];
    while (stack.length) {
      var pixel = stack.pop();
      var px = pixel % width;
      var py = Math.floor(pixel / width);
      var index = pixel * 4;
      if (!pixelMatches(data, index, target)) continue;
      setPixel(data, index, fill);
      if (px > 0) stack.push(pixel - 1);
      if (px < width - 1) stack.push(pixel + 1);
      if (py > 0) stack.push(pixel - width);
      if (py < height - 1) stack.push(pixel + width);
    }
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
    if (!isCanvasEditablePhase()) return '';
    if (state.brushMode === 'brush' && state.brushToolKind === 'bucket') return '';
    return state.brushMode === 'eraser' ? 'eraser' : 'brush';
  }

  function isPointerInsideCanvas(event) {
    if (!event || !els.canvas || !isCanvasEditablePhase()) return false;
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

  function setDebugPanelPosition(left, top) {
    if (!els.debugPanel) return;
    var width = els.debugPanel.offsetWidth || 360;
    var height = els.debugPanel.offsetHeight || 360;
    var maxLeft = Math.max(8, window.innerWidth - width - 8);
    var maxTop = Math.max(8, window.innerHeight - height - 8);
    els.debugPanel.style.left = Math.max(8, Math.min(maxLeft, left)) + 'px';
    els.debugPanel.style.top = Math.max(8, Math.min(maxTop, top)) + 'px';
    els.debugPanel.style.right = 'auto';
  }

  function beginDebugPanelDrag(event) {
    if (!els.debugPanel || !els.debugPanelHandle || event.button !== 0) return;
    if (event.target && event.target.closest && event.target.closest('button,input,select')) return;
    event.preventDefault();
    var rect = els.debugPanel.getBoundingClientRect();
    state.debugPanelDrag = {
      pointerId: event.pointerId,
      dx: event.clientX - rect.left,
      dy: event.clientY - rect.top
    };
    els.debugPanel.classList.add('is-dragging');
    try { els.debugPanelHandle.setPointerCapture(event.pointerId); } catch (_) {}
  }

  function moveDebugPanelDrag(event) {
    if (!state.debugPanelDrag || state.debugPanelDrag.pointerId !== event.pointerId) return;
    event.preventDefault();
    setDebugPanelPosition(event.clientX - state.debugPanelDrag.dx, event.clientY - state.debugPanelDrag.dy);
  }

  function endDebugPanelDrag(event) {
    if (!state.debugPanelDrag || state.debugPanelDrag.pointerId !== event.pointerId) return;
    event.preventDefault();
    state.debugPanelDrag = null;
    if (els.debugPanel) els.debugPanel.classList.remove('is-dragging');
    try { els.debugPanelHandle.releasePointerCapture(event.pointerId); } catch (_) {}
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
    if (!isCanvasEditablePhase() || event.button !== 0) return;
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
    if (!state.isDrawing || !isCanvasEditablePhase()) return;
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

  function handleChatSubmit(event) {
    event.preventDefault();
    var value = String(els.chatInput.value || '').trim();
    if (!value) return;
    els.chatInput.value = '';
    addUserMessage(value);
    if (state.phase === 'user_guessing') {
      submitUserGuess(value);
    } else if (state.phase === 'drawing_pick') {
      submitGameChat(value);
    } else if (state.phase === 'user_drawing') {
      submitGameChat(value);
    } else if (state.phase === 'ai_guess_feedback') {
      submitFeedbackInput(value);
    } else if (state.phase === 'summary') {
      submitGameChat(value);
    } else if (state.phase === 'final_summary') {
      submitGameChat(value, { summaryChatOnly: true });
    }
  }

  function handleVoiceRouteButton() {
    if (!state.routeActive) {
      addEventMessage('drawingGuess.voice.routeNotReady', 'Wait until the game route is ready before using voice.');
      return;
    }
    if (state.voiceRouteActive) {
      addEventMessage('drawingGuess.voice.connectedNotice', 'Voice chat is connected to this round.');
      return;
    }
    addEventMessage('drawingGuess.voice.connectHintNotice', 'Open voice on the main page first; this round will take it over when it becomes active.');
  }

  function finishGame() {
    renderFinalSummary();
    showExitConfirm();
  }

  function bindEvents() {
    els.tutorialStartButton.addEventListener('click', startGame);
    els.nextRoundButton.addEventListener('click', startNextRound);
    els.endButton.addEventListener('click', finishGame);
    if (els.debugTrigger) {
      els.debugTrigger.addEventListener('click', function () {
        shakeDebugTrigger();
        recordDebugGesture('L');
      });
      els.debugTrigger.addEventListener('contextmenu', function (event) {
        event.preventDefault();
        recordDebugGesture('R');
      });
    }
    if (els.debugClose) els.debugClose.addEventListener('click', closeDebugPanel);
    if (els.debugPanelHandle) {
      els.debugPanelHandle.addEventListener('pointerdown', beginDebugPanelDrag);
      els.debugPanelHandle.addEventListener('pointermove', moveDebugPanelDrag);
      els.debugPanelHandle.addEventListener('pointerup', endDebugPanelDrag);
      els.debugPanelHandle.addEventListener('pointercancel', endDebugPanelDrag);
    }
    if (els.debugCharacterSelect) {
      els.debugCharacterSelect.addEventListener('change', function () {
        switchDebugCharacter(els.debugCharacterSelect.value);
      });
    }
    if (els.debugAiRound) {
      els.debugAiRound.addEventListener('click', function () {
        startDebugAiRound(false);
      });
    }
    if (els.debugUserRound) {
      els.debugUserRound.addEventListener('click', function () {
        startDebugUserRound(false);
      });
    }
    if (els.debugRotateRounds) {
      els.debugRotateRounds.addEventListener('change', function () {
        state.debugRotateRounds = !!els.debugRotateRounds.checked;
        syncDebugPanelState();
      });
    }
    if (els.debugTriggerAiGuess) {
      els.debugTriggerAiGuess.addEventListener('click', triggerDebugAiGuessNow);
    }
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
    window.addEventListener('beforeunload', function () {
      if (state.routeActive) endRoute(true);
    });
    window.addEventListener('localechange', function () {
      updateControls();
      setPhase(state.phase);
      syncBrushToolButton();
    });
    window.addEventListener('resize', function () {
      requestAnimationFrame(function () {
        applySideSplitRatio(state.sideSplitRatio, false);
      });
    });
    window.addEventListener('neko-speech-playback-state', handleSpeechPlaybackState);
  }

  function init() {
    initEls();
    loadColorHistory();
    setBrushColor(currentBrushColor());
    renderColorHistory();
    syncBrushToolButton();
    state.sessionId = String(boot.sessionId || '') || makeSessionId();
    state.lanlanName = String(boot.lanlanName || (window.lanlan_config && window.lanlan_config.lanlan_name) || '').trim();
    state.windowLanlanName = state.lanlanName;
    loadModelViewSettings();
    loadSideSplitRatio();
    resetCanvas();
    showPlaceholder();
    setPhase('tutorial');
    readMemoryConsent();
    bindEvents();
    startDebugCountdownUpdater();
    loadCurrentCharacter();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
