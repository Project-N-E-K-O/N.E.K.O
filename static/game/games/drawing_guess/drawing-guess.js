(function () {
  'use strict';

  var GAME_TYPE = 'drawing_guess';
  var ROUTE_API = '/api/game/' + GAME_TYPE;
  var ROUND_API = '/api/game/drawing_guess';
  var ROUND_FALLBACK_SECONDS = 5 * 60;
  var AI_DRAW_REQUEST_TIMEOUT_MS = 70 * 1000;
  var AI_GUESS_REQUEST_TIMEOUT_MS = ROUND_FALLBACK_SECONDS * 1000 + 10000;
  var AI_GUESS_MIN_DELAY_MS = 10000;
  var AI_GUESS_MAX_DELAY_MS = 60000;
  var DRAW_PICK_DURATION_MS = 1450;
  var AI_DRAWING_PLACEHOLDER_DELAY_MS = 1200;
  var boot = window.__DRAWING_GUESS_BOOT__ || {};

  var state = {
    sessionId: '',
    lanlanName: '',
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
    colorWheelPointerId: null,
    colorHistory: [],
    drawPickTimer: null,
    drawPickRevealTimer: null,
    aiGuessTimer: null,
    aiGuessNextAt: 0,
    nekoVoiceQueue: [],
    nekoVoiceInFlight: false,
    speechAudioSocket: null,
    speechAudioReconnectTimer: null,
    speechAudioPingTimer: null,
    speechAudioManualClose: false,
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
    roundFlowToken: 0,
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
    live2dManager: null,
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
      debugClose: $('debug-close'),
      debugCharacterSelect: $('debug-character-select'),
      debugAiRound: $('debug-ai-round'),
      debugUserRound: $('debug-user-round'),
      debugRotateRounds: $('debug-rotate-rounds'),
      debugAiGuessCountdown: $('debug-ai-guess-countdown'),
      debugTriggerAiGuess: $('debug-trigger-ai-guess'),
      modelStage: $('model-stage'),
      sidePane: $('side-pane'),
      sideResizer: $('side-resizer'),
      live2dContainer: $('live2d-container'),
      live2dCanvas: $('live2d-canvas'),
      vrmContainer: $('vrm-container'),
      vrmCanvas: $('vrm-canvas'),
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
      requestAnimationFrame(function () {
        if (state.modelKind === 'live2d') fitLive2DModelToSlot();
      });
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
    var rect = els.modelStage.getBoundingClientRect();
    var width = Math.max(1, rect.width || 1);
    var height = Math.max(1, rect.height || 1);
    var dx = (event.clientX - state.modelDrag.x) / width * 100;
    var dy = (event.clientY - state.modelDrag.y) / height * 100;
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
      ['loading', els.modelLoading],
      ['fallback', els.modelFallback]
    ].forEach(function (pair) {
      var node = pair[1];
      if (!node) return;
      node.hidden = pair[0] !== normalized;
    });
    setModelKind(normalized);
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

  function updateDebugGuessCountdown() {
    if (!els.debugAiGuessCountdown) return;
    var text = '--';
    if (state.phase === 'ai_guess_feedback') {
      if (state.pendingAutoGuess && state.chatInFlight) {
        text = '等待聊天结束';
      } else if (state.aiGuessInFlight) {
        text = '猜测中';
      } else if (state.aiGuessTimer && state.aiGuessNextAt) {
        text = formatDebugCountdown(state.aiGuessNextAt - Date.now());
      } else if (state.aiGuessAttempts >= state.maxAiGuessAttempts) {
        text = '次数已用完';
      } else {
        text = '未安排';
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
    updateDebugGuessCountdown();
  }

  function startDebugCountdownUpdater() {
    clearInterval(state.debugCountdownTimer);
    state.debugCountdownTimer = setInterval(updateDebugGuessCountdown, 300);
  }

  function beginRoundFlow() {
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
      postgameProactive: false
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
      state.debugRoundMode = 'auto';
      window.lanlan_config = window.lanlan_config || {};
      window.lanlan_config.lanlan_name = nextName;
      loadModelViewSettings();
      initModelSlotForCurrentCharacter(nextName).catch(function () {});
      showPlaceholder();
      setPhase('loading_round');
      addEventMessage('', '调试：已切换为 ' + nextName + '，请选择要测试的回合。');
      return startRoute();
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
    appState.pendingAudioChunkMetaQueue.push({
      speechId: speechId,
      turnId: String((response && (response.turn_id || response.turnId)) || speechId),
      shouldSkip: false,
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
    }, 1200);
  }

  function startSpeechAudioSocket() {
    if (!window.WebSocket || !state.routeActive || state.routeEnding || !state.lanlanName || !state.sessionId) return;
    var existing = state.speechAudioSocket;
    if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) return;
    clearSpeechAudioTimers();
    state.speechAudioManualClose = false;
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
      clearSpeechAudioTimers();
      scheduleSpeechAudioReconnect();
    };
    socket.onerror = function () {};
  }

  function stopSpeechAudioSocket() {
    state.speechAudioManualClose = true;
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
      request_id: item.requestId,
      mirror_text: false,
      emit_turn_end: true,
      interrupt_audio: false,
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
    if (!dataUrl || dataUrl.length > 1800000) return {};
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

  function roundPayload(extra) {
    return Object.assign({
      session_id: state.sessionId,
      lanlan_name: state.lanlanName,
      i18n_language: currentLanguage(),
      memory_consent: state.memoryConsent,
      client_round_token: state.roundFlowToken
    }, extra || {});
  }

  function hideAllStageViews() {
    clearAiDrawingPlaceholderHint(false);
    hideSizePreview();
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

  function measureSvgContentBounds(svg, viewBox) {
    var svgRect = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
    if (!svgRect || svgRect.width <= 0 || svgRect.height <= 0) return null;
    return Array.prototype.slice.call(svg.querySelectorAll('path,line,polyline,polygon,rect,circle,ellipse')).reduce(function (bounds, node) {
      var rect = null;
      try {
        rect = node.getBoundingClientRect();
      } catch (_) {}
      if (!rect || rect.width < 0.5 || rect.height < 0.5) return bounds;
      if (isFullCanvasRect(node, viewBox, rect, svgRect)) return bounds;
      var nodeBounds = screenRectToSvgBounds(svg, rect);
      if (!nodeBounds) return bounds;
      if (!bounds) return nodeBounds;
      bounds.x1 = Math.min(bounds.x1, nodeBounds.x1);
      bounds.y1 = Math.min(bounds.y1, nodeBounds.y1);
      bounds.x2 = Math.max(bounds.x2, nodeBounds.x2);
      bounds.y2 = Math.max(bounds.y2, nodeBounds.y2);
      return bounds;
    }, null);
  }

  function fitAiDrawingSvgToContent(svg) {
    if (!svg) return;
    var viewBox = parseSvgViewBox(svg);
    var viewBoxRatio = 240 / 180;
    var bounds = measureSvgContentBounds(svg, viewBox);
    if (!bounds) return;
    var contentWidth = Math.max(1, bounds.x2 - bounds.x1);
    var contentHeight = Math.max(1, bounds.y2 - bounds.y1);
    var centerX = (bounds.x1 + bounds.x2) / 2;
    var centerY = (bounds.y1 + bounds.y2) / 2;
    var maxContentRatio = 0.62;
    var nextWidth = Math.max(contentWidth / maxContentRatio, (contentHeight / maxContentRatio) * viewBoxRatio);
    var nextHeight = nextWidth / viewBoxRatio;
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

  function applyExternalDrawingResult(res) {
    if (!res || !res.ok) return;
    if (res.kind === 'guess' && res.correct) {
      stopCountdown();
      state.aiAnswerLabel = res.answer ? String(res.answer.label || '') : '';
      addEventMessage('drawingGuess.messages.answerReveal', 'Answer: {{answer}}', {
        answer: res.answer ? res.answer.label : ''
      });
      continueAfterAiDrawingHalf(res);
      return;
    }
    if (res.kind === 'ai_guess') {
      var guessLabel = res.guess ? res.guess.label : '';
      stopThinkingEventMessage();
      state.pendingAutoGuess = false;
      state.aiGuessAttempts = Number(res.attempt || state.aiGuessAttempts || 0);
      state.maxAiGuessAttempts = Number(res.max_attempts || state.maxAiGuessAttempts || 3);
      addEventMessage('drawingGuess.messages.aiGuessLine', 'She guessed: {{guess}}', { guess: guessLabel });
      if (res.state && res.state.phase === 'summary') {
        renderSummary(res);
      } else {
        setPhase('ai_guess_feedback');
        setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Keep chatting; give a hint when you want her to guess again');
        addEventMessage('drawingGuess.messages.aiNeedsHint', 'You can just keep chatting. Give her a hint when you want another guess.');
        scheduleNextRandomAiGuess();
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
    if (output.type === 'game_external_input') {
      var inputText = externalInputText(output);
      var requestId = String(output.request_id || '');
      if (inputText && requestId !== state.lastExternalInputRequestId) {
        state.lastExternalInputRequestId = requestId;
        addUserMessage(inputText);
      }
      return;
    }
    if (output.type !== 'game_llm_result') return;
    var result = output.result || {};
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
      reason: options.finalSummary || state.phase === 'summary' ? 'drawing_guess_game_over' : 'drawing_guess_abandoned',
      roundCompleted: !!options.finalSummary || state.phase === 'summary',
      round_completed: !!options.finalSummary || state.phase === 'summary',
      postgameProactive: false
    }));
    if (useBeacon && navigator.sendBeacon) {
      try {
        navigator.sendBeacon(ROUTE_API + '/route/end', new Blob([payload], { type: 'application/json' }));
        return Promise.resolve({ ok: true, beacon: true });
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
      return els.canvas.toDataURL('image/png');
    } catch (_) {
      return '';
    }
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

  function settleAiGuessTimeout() {
    stopAiGuessSchedule();
    if (state.phase !== 'ai_guessing' && state.phase !== 'ai_guess_feedback') return;
    return post(ROUND_API + '/timeout', roundPayload(), 10000).then(function (res) {
      if (!res || !res.ok) return;
      if (res.message) addNekoMessage(res.message);
      if (res.phase === 'summary' || (res.state && res.state.phase === 'summary')) {
        renderSummary(res);
      }
    }).catch(function () {}).finally(updateControls);
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
      if (ok) startRound();
    });
  }

  function resetRoundStartState() {
    var token = beginRoundFlow();
    hideExitReopenButton();
    hideExitConfirm(false);
    stopCountdown();
    resetCanvas();
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
        if (!res || !res.ok) throw new Error((res && res.reason) || 'ai_draw_failed');
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
        addMessage('drawingGuess.messages.roundFailed', 'Round failed: {{reason}}', { reason: readableRequestError(err) });
      })
      .finally(updateControls);
  }

  function shouldStayOnDebugAiRound() {
    return !state.debugRotateRounds && state.debugRoundMode === 'ai';
  }

  function continueAfterAiDrawingHalf(res) {
    if (shouldStayOnDebugAiRound()) {
      stopCountdown();
      addEventMessage('', '调试：保持猫娘回合，准备下一题。');
      setTimeout(function () {
        startDebugAiRound(true);
      }, 450);
      return;
    }
    prepareUserDrawing(res.user_draw_options || res.user_draw_answer, res.draw_seconds || ROUND_FALLBACK_SECONDS);
  }

  function handleGuessTimeout() {
    post(ROUND_API + '/timeout', roundPayload(), 10000).then(function (res) {
      if (res && res.ok) {
        addNekoMessage(res.message || t('drawingGuess.messages.guessTimeout', 'Time is up. The answer was {{answer}}.', {
          answer: res.answer ? res.answer.label : ''
        }));
        state.aiAnswerLabel = res.answer ? String(res.answer.label || '') : '';
        addEventMessage('drawingGuess.messages.answerReveal', 'Answer: {{answer}}', { answer: res.answer ? res.answer.label : '' });
        continueAfterAiDrawingHalf(res);
      }
    }).catch(function () {});
  }

  function submitUserGuess(text) {
    return post(ROUND_API + '/input', roundPayload({ text: text }), 10000).then(function (res) {
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      addNekoMessage(res.message || '');
      if (res.correct) {
        stopCountdown();
        state.aiAnswerLabel = res.answer ? String(res.answer.label || '') : '';
        addEventMessage('drawingGuess.messages.answerReveal', 'Answer: {{answer}}', {
          answer: res.answer ? res.answer.label : ''
        });
        continueAfterAiDrawingHalf(res);
      }
    }).finally(function () {
      stopThinkingEventMessage();
      updateControls();
    });
  }

  function submitGameChat(text) {
    state.chatInFlight = true;
    return post(ROUND_API + '/input', roundPayload({ text: text }), 20000).then(function (res) {
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      addNekoMessage(res.message);
    }).finally(function () {
      state.chatInFlight = false;
      flushDeferredAiGuessWork();
      updateControls();
    });
  }

  function submitFeedbackInput(text) {
    state.chatInFlight = true;
    return post(ROUND_API + '/input', roundPayload({
      text: text,
      image_data_url: state.userPng
    }), AI_GUESS_REQUEST_TIMEOUT_MS).then(function (res) {
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
        if (res.state && res.state.phase === 'summary') {
          renderSummary(res);
        } else {
          setPhase('ai_guess_feedback');
          setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Keep chatting; give a hint when you want her to guess again');
          addEventMessage('drawingGuess.messages.aiNeedsHint', 'You can just keep chatting. Give her a hint when you want another guess.');
          scheduleNextRandomAiGuess();
        }
        return;
      }
      addNekoMessage(res.message);
    }).finally(function () {
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

    return post(ROUND_API + '/choose-word', roundPayload({ word_id: wordId }), 10000).then(function (res) {
      if (!res || !res.ok) {
        state.drawPickChoosing = false;
        renderDrawPickOptions(state.drawPickOptions);
        els.drawPick.classList.remove('dg-draw-pick-spread');
        els.drawPick.classList.add('dg-draw-pick-ready', 'dg-draw-pick-revealed');
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      beginUserDrawing(res.user_draw_answer || selected, res.draw_seconds || state.drawPickSeconds || ROUND_FALLBACK_SECONDS);
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
    var imageDataUrl = (options && options.image_data_url) || state.userPng;
    return post(ROUND_API + '/vision-guess', roundPayload({
      image_data_url: imageDataUrl,
      user_hint: userHint || '',
      settle_on_miss: !!(options && options.settle_on_miss),
      time_expired: !!(options && options.settle_on_miss)
    }), AI_GUESS_REQUEST_TIMEOUT_MS).then(function (res) {
      stopThinkingEventMessage();
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      var guessLabel = res.guess ? res.guess.label : '';
      state.pendingAutoGuess = false;
      state.aiGuessAttempts = Number(res.attempt || state.aiGuessAttempts || 0);
      state.maxAiGuessAttempts = Number(res.max_attempts || state.maxAiGuessAttempts || 3);
      addNekoMessage(res.message);
      addEventMessage('drawingGuess.messages.aiGuessLine', 'She guessed: {{guess}}', { guess: guessLabel });
      if (res.state && res.state.phase === 'summary') {
        renderSummary(res);
      } else {
        setPhase('ai_guess_feedback');
        setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Keep chatting; give a hint when you want her to guess again');
        addEventMessage('drawingGuess.messages.aiNeedsHint', 'You can just keep chatting. Give her a hint when you want another guess.');
        scheduleNextRandomAiGuess();
      }
    }).finally(function () {
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
    stopCountdown();
    stopThinkingEventMessage();
    stopDrawPickAnimation();
    stopAiGuessSchedule();
    showSummary();
    setPhase('final_summary');
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
    Array.prototype.slice.call(els.summary.querySelectorAll('[data-download-ai-index]')).forEach(function (button) {
      button.addEventListener('click', function () {
        var summary = list[Number(button.getAttribute('data-download-ai-index') || 0)];
        if (summary) downloadBlob('neko-drawing-round-' + summary.round + '.svg', summary.aiSvg || '', 'image/svg+xml;charset=utf-8');
      });
    });
    Array.prototype.slice.call(els.summary.querySelectorAll('[data-download-user-index]')).forEach(function (button) {
      button.addEventListener('click', function () {
        var summary = list[Number(button.getAttribute('data-download-user-index') || 0)];
        if (summary && summary.userPng) {
          fetch(summary.userPng).then(function (res) { return res.blob(); }).then(function (blob) {
            downloadBlob('your-drawing-round-' + summary.round + '.png', blob, 'image/png');
          }).catch(function () {});
        }
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
      + '<section class="dg-thumb"><h4>' + escapeHtml(nekoTitle) + '</h4><div class="dg-thumb-preview" data-summary-ai-art="' + index + '"></div><button class="dg-button" type="button" data-download-ai-index="' + index + '">' + escapeHtml(t('drawingGuess.actions.downloadSvg', 'Download SVG')) + '</button></section>'
      + '<section class="dg-thumb"><h4>' + escapeHtml(userTitle) + '</h4><div class="dg-thumb-preview">' + (summary.userPng ? '<img alt="" src="' + escapeAttr(summary.userPng) + '">' : '') + '</div><button class="dg-button" type="button" data-download-user-index="' + index + '">' + escapeHtml(t('drawingGuess.actions.downloadPng', 'Download PNG')) + '</button></section>'
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

  function downloadAiSvg() {
    downloadBlob('neko-drawing.svg', state.aiSvg || '', 'image/svg+xml;charset=utf-8');
  }

  function downloadUserPng() {
    fetch(state.userPng).then(function (res) { return res.blob(); }).then(function (blob) {
      downloadBlob('your-drawing.png', blob, 'image/png');
    }).catch(function () {});
  }

  function cleanConfigValue(value) {
    if (value == null) return '';
    var text = String(value).trim();
    var lower = text.toLowerCase();
    if (!text || lower === 'undefined' || lower === 'null') return '';
    return text;
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
    var modelType = cleanConfigValue(character && character.model_type) || cleanConfigValue(avatar.model_type) || 'live2d';
    var live3dSubType = cleanConfigValue(character && character.live3d_sub_type) || cleanConfigValue(avatar.live3d_sub_type);
    var vrmPath = cleanConfigValue(character && character.vrm) || cleanConfigValue(vrm.model_path);
    var mmdPath = cleanConfigValue(character && character.mmd) || cleanConfigValue(mmd.model_path);
    var live2dPath = cleanConfigValue(character && character.live2d) || cleanConfigValue(live2d.model_path);
    var effectiveKind = modelType.toLowerCase();
    if (effectiveKind === 'live3d') {
      if (live3dSubType.toLowerCase() === 'mmd') {
        effectiveKind = 'mmd';
      } else if (live3dSubType.toLowerCase() === 'vrm' || vrmPath) {
        effectiveKind = 'vrm';
      }
    } else if (!effectiveKind || effectiveKind === 'default') {
      effectiveKind = vrmPath ? 'vrm' : 'live2d';
    }
    return {
      modelType: modelType,
      live3dSubType: live3dSubType,
      effectiveKind: effectiveKind,
      live2dPath: live2dPath,
      vrmPath: vrmPath,
      mmdPath: mmdPath,
      lighting: character && character.lighting,
      vrmIdleAnimation: character && character.idleAnimation,
      vrmIdleAnimations: character && character.idleAnimations
    };
  }

  function syncLanlanAvatarConfig(config) {
    window.lanlan_config = window.lanlan_config || {};
    window.lanlan_config.model_type = config.modelType || '';
    window.lanlan_config.live3d_sub_type = config.live3dSubType || '';
    if (config.vrmPath) {
      window.lanlan_config.vrm = config.vrmPath;
      window.vrmModel = config.vrmPath;
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
      if (state.modelKind !== 'live2d') return;
      requestAnimationFrame(fitLive2DModelToSlot);
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
    ctx.fillStyle = '#fffdfa';
    ctx.fillRect(0, 0, els.canvas.width, els.canvas.height);
    ctx.restore();
    state.hasDrawn = false;
    state.history = [];
    state.redo = [];
    pushHistory();
    updateControls();
  }

  function pushHistory() {
    try {
      state.history.push(els.ctx.getImageData(0, 0, els.canvas.width, els.canvas.height));
      if (state.history.length > 30) state.history.shift();
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
    updateControls();
  }

  function redoCanvas() {
    if (!state.redo.length) return;
    var image = state.redo.pop();
    state.history.push(image);
    restoreImage(image);
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
      ? loaded.map(normalizeHexColor).filter(Boolean).slice(0, 14)
      : [];
  }

  function saveColorHistory() {
    try {
      localStorage.setItem(colorHistoryStorageKey(), JSON.stringify(state.colorHistory.slice(0, 14)));
    } catch (_) {}
  }

  function renderColorHistory() {
    if (!els.colorHistoryColors) return;
    els.colorHistoryColors.innerHTML = '';
    state.colorHistory.slice(0, 14).forEach(function (color) {
      var button = document.createElement('button');
      button.className = 'dg-color-chip';
      button.type = 'button';
      button.dataset.color = color;
      button.title = color;
      button.style.setProperty('--chip-color', color);
      button.addEventListener('click', function () {
        setBrushColor(color, { remember: true });
      });
      els.colorHistoryColors.appendChild(button);
    });
  }

  function rememberBrushColor(color) {
    var normalized = normalizeHexColor(color);
    state.colorHistory = [normalized].concat(state.colorHistory.filter(function (item) {
      return normalizeHexColor(item) !== normalized;
    })).slice(0, 14);
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
      showSizePreview('brush');
    }
    if (options && options.remember) rememberBrushColor(normalized);
  }

  function setColorWheelCursor(x, y) {
    if (!els.colorWheel) return;
    els.colorWheel.style.setProperty('--dg-color-cursor-x', x + '%');
    els.colorWheel.style.setProperty('--dg-color-cursor-y', y + '%');
  }

  function pickColorFromWheel(event, remember) {
    if (!els.colorWheel) return;
    var rect = els.colorWheel.getBoundingClientRect();
    var radius = Math.max(1, Math.min(rect.width, rect.height) / 2);
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    var dx = event.clientX - cx;
    var dy = event.clientY - cy;
    var distance = Math.sqrt(dx * dx + dy * dy);
    var clampedDistance = Math.min(radius, distance);
    var angle = Math.atan2(dy, dx);
    var x = Math.cos(angle) * clampedDistance;
    var y = Math.sin(angle) * clampedDistance;
    var hue = (angle * 180 / Math.PI + 360) % 360;
    var saturation = clampedDistance / radius;
    setColorWheelCursor(50 + (x / radius) * 50, 50 + (y / radius) * 50);
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
  }

  function showSizePreview(tool) {
    if (!els.sizePreview || !els.canvas) return;
    var kind = tool === 'eraser' ? 'eraser' : 'brush';
    var size = Number(kind === 'eraser' ? (els.eraserSize.value || 13) : (els.brushSize.value || 7));
    var canvasHidden = els.canvas.classList.contains('dg-hidden');
    var rect = canvasHidden && els.canvasStage
      ? els.canvasStage.getBoundingClientRect()
      : els.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    var scaleX = rect.width / Math.max(1, els.canvas.width || 800);
    var scaleY = rect.height / Math.max(1, els.canvas.height || 600);
    var diameter = Math.max(2, size * ((scaleX + scaleY) / 2));
    var borderWidth = Math.max(1, Math.min(3, diameter / 5));
    els.sizePreview.dataset.previewTool = kind;
    els.sizePreview.style.setProperty('--dg-size-preview-diameter', diameter.toFixed(2) + 'px');
    els.sizePreview.style.setProperty('--dg-size-preview-border', borderWidth.toFixed(2) + 'px');
    if (kind === 'brush') {
      els.sizePreview.style.setProperty('--dg-size-preview-color', currentBrushColor());
    } else {
      els.sizePreview.style.removeProperty('--dg-size-preview-color');
    }
    els.sizePreview.hidden = false;
    clearTimeout(state.sizePreviewTimer);
    state.sizePreviewTimer = setTimeout(hideSizePreview, 2600);
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

  function beginStroke(event) {
    if (!isCanvasEditablePhase()) return;
    event.preventDefault();
    var p = canvasPoint(event);
    if (state.brushMode === 'brush' && state.brushToolKind === 'bucket') {
      if (floodFillCanvas(p)) {
        state.hasDrawn = true;
        pushHistory();
      }
      return;
    }
    state.isDrawing = true;
    els.ctx.beginPath();
    els.ctx.moveTo(p.x, p.y);
    try { els.canvas.setPointerCapture(event.pointerId); } catch (_) {}
  }

  function moveStroke(event) {
    if (!state.isDrawing || !isCanvasEditablePhase()) return;
    event.preventDefault();
    var p = canvasPoint(event);
    els.ctx.lineCap = 'round';
    els.ctx.lineJoin = 'round';
    if (state.brushMode === 'eraser') {
      els.ctx.globalCompositeOperation = 'destination-out';
      els.ctx.strokeStyle = 'rgba(0,0,0,1)';
      els.ctx.lineWidth = Number(els.eraserSize.value || 13);
    } else {
      els.ctx.globalCompositeOperation = 'source-over';
      els.ctx.strokeStyle = currentBrushColor();
      els.ctx.lineWidth = Number(els.brushSize.value || 7);
    }
    els.ctx.lineTo(p.x, p.y);
    els.ctx.stroke();
    state.hasDrawn = true;
  }

  function endStroke(event) {
    if (!state.isDrawing) return;
    event.preventDefault();
    state.isDrawing = false;
    els.ctx.globalCompositeOperation = 'source-over';
    pushHistory();
  }

  function setTool(tool) {
    state.brushMode = tool;
    els.brushTool.setAttribute('aria-pressed', tool === 'brush' ? 'true' : 'false');
    els.eraserTool.setAttribute('aria-pressed', tool === 'eraser' ? 'true' : 'false');
    if (tool === 'eraser') {
      showSizePreview('eraser');
    } else if (state.brushToolKind === 'brush') {
      showSizePreview('brush');
    } else {
      hideSizePreview();
    }
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
    }
  }

  function bindToolPopoverEvents() {
    document.querySelectorAll('.dg-tool-popover').forEach(function (popover) {
      popover.addEventListener('pointerenter', function () {
        openToolPopover(popover);
      });
      popover.addEventListener('pointerleave', function () {
        popover.classList.remove('is-open');
      });
      popover.addEventListener('focusin', function () {
        openToolPopover(popover);
      });
      popover.addEventListener('focusout', function (event) {
        if (event.relatedTarget && popover.contains(event.relatedTarget)) return;
        popover.classList.remove('is-open');
      });
      popover.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
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
    } else if (state.phase === 'summary' || state.phase === 'final_summary') {
      submitGameChat(value);
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
    return endRoute(false, { finalSummary: true }).finally(showExitConfirm);
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
  }

  function init() {
    initEls();
    loadColorHistory();
    setBrushColor(currentBrushColor());
    renderColorHistory();
    syncBrushToolButton();
    state.sessionId = String(boot.sessionId || '') || makeSessionId();
    state.lanlanName = String(boot.lanlanName || (window.lanlan_config && window.lanlan_config.lanlan_name) || '').trim();
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
