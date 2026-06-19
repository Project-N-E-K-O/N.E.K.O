(function () {
  'use strict';

  var GAME_TYPE = 'drawing_guess';
  var ROUTE_API = '/api/game/' + GAME_TYPE;
  var ROUND_API = '/api/game/drawing_guess';
  var boot = window.__DRAWING_GUESS_BOOT__ || {};

  var state = {
    sessionId: '',
    lanlanName: '',
    routeActive: false,
    routeEnding: false,
    heartbeatTimer: null,
    countdownTimer: null,
    phase: 'tutorial',
    memoryConsent: 'none',
    aiSvg: '',
    userPng: '',
    userDrawAnswer: null,
    brushMode: 'brush',
    isDrawing: false,
    hasDrawn: false,
    history: [],
    redo: []
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
      modelState: $('model-state'),
      memoryState: $('memory-state'),
      startButton: $('start-button'),
      doneButton: $('done-button'),
      nextRoundButton: $('next-round-button'),
      endButton: $('end-button'),
      clearCanvasButton: $('clear-canvas-button'),
      reloadCharacterButton: $('reload-character-button'),
      tutorialOverlay: $('tutorial-overlay'),
      tutorialStartButton: $('tutorial-start-button'),
      messageLog: $('message-log'),
      chatForm: $('chat-form'),
      chatInput: $('chat-input'),
      chatSubmit: document.querySelector('#chat-form button[type="submit"]'),
      placeholder: $('canvas-placeholder'),
      aiDrawing: $('ai-drawing'),
      canvas: $('user-canvas'),
      summary: $('summary-view'),
      badge: $('canvas-badge'),
      brushTool: $('brush-tool'),
      eraserTool: $('eraser-tool'),
      undoTool: $('undo-tool'),
      redoTool: $('redo-tool'),
      brushColor: $('brush-color'),
      brushSize: $('brush-size')
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

  function makeSessionId() {
    return 'drawing-guess-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  }

  function setStatus(key, fallback) {
    els.routeStatus.setAttribute('data-i18n', 'drawingGuess.status.' + key);
    els.routeStatus.textContent = t('drawingGuess.status.' + key, fallback);
  }

  function setPhase(phase) {
    state.phase = phase;
    els.modelState.setAttribute('data-i18n', 'drawingGuess.phases.' + phase);
    els.modelState.textContent = t('drawingGuess.phases.' + phase, phase);
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
    els.chatInput.placeholder = t(key, fallback);
  }

  function updateControls() {
    var routeReady = !!state.lanlanName && !state.routeEnding;
    var activeRound = ['ai_drawing', 'user_guessing', 'user_drawing', 'ai_guessing', 'ai_guess_feedback'].indexOf(state.phase) >= 0;
    els.characterName.textContent = state.lanlanName || '-';
    els.sessionId.textContent = state.sessionId || '-';
    els.memoryState.setAttribute('data-i18n', 'drawingGuess.memory.' + state.memoryConsent + 'Short');
    els.memoryState.textContent = t('drawingGuess.memory.' + state.memoryConsent + 'Short', state.memoryConsent);
    els.startButton.disabled = !routeReady || activeRound;
    els.endButton.disabled = !state.routeActive || state.routeEnding;
    els.doneButton.disabled = state.phase !== 'user_drawing';
    els.clearCanvasButton.disabled = state.phase !== 'user_drawing';
    els.nextRoundButton.disabled = state.phase !== 'summary' || !state.routeActive;
    els.chatSubmit.disabled = state.phase !== 'user_guessing' && state.phase !== 'user_drawing' && state.phase !== 'ai_guess_feedback';
    els.chatInput.disabled = els.chatSubmit.disabled;
    els.undoTool.disabled = state.phase !== 'user_drawing' || state.history.length <= 1;
    els.redoTool.disabled = state.phase !== 'user_drawing' || state.redo.length === 0;
  }

  function addMessage(key, fallback, params, className) {
    var node = document.createElement('div');
    node.className = 'dg-message ' + (className || 'dg-message-system');
    node.textContent = key ? t(key, fallback, params || {}) : String(fallback || '');
    els.messageLog.appendChild(node);
    els.messageLog.scrollTop = els.messageLog.scrollHeight;
  }

  function post(url, payload, timeoutMs) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeoutMs || 10000);
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
      signal: controller.signal
    }).then(function (res) {
      return res.json().catch(function () { return { ok: res.ok }; });
    }).finally(function () {
      clearTimeout(timer);
    });
  }

  function routePayload(extra) {
    return Object.assign({
      session_id: state.sessionId,
      lanlan_name: state.lanlanName,
      source: 'drawing_guess_demo',
      game_type: GAME_TYPE,
      i18n_language: currentLanguage(),
      gameStarted: state.phase !== 'tutorial',
      game_started: state.phase !== 'tutorial',
      memory_consent: state.memoryConsent,
      currentState: {
        game: GAME_TYPE,
        phase: state.phase,
        memory_consent: state.memoryConsent
      }
    }, extra || {});
  }

  function roundPayload(extra) {
    return Object.assign({
      session_id: state.sessionId,
      lanlan_name: state.lanlanName,
      i18n_language: currentLanguage(),
      memory_consent: state.memoryConsent
    }, extra || {});
  }

  function hideAllStageViews() {
    els.placeholder.classList.add('dg-hidden');
    els.aiDrawing.classList.add('dg-hidden');
    els.canvas.classList.add('dg-hidden');
    els.summary.classList.add('dg-hidden');
  }

  function showPlaceholder() {
    stopCountdown();
    hideAllStageViews();
    els.placeholder.classList.remove('dg-hidden');
    setBadge('');
  }

  function showAiDrawing(svg) {
    hideAllStageViews();
    els.aiDrawing.innerHTML = svg || '';
    normalizeAiDrawingSvg();
    els.aiDrawing.style.visibility = 'hidden';
    els.aiDrawing.classList.remove('dg-hidden');
    requestAnimationFrame(function () {
      fitAiDrawingSvgToContent();
      els.aiDrawing.style.visibility = '';
      animateAiDrawing();
    });
  }

  function normalizeAiDrawingSvg() {
    var svg = els.aiDrawing.querySelector('svg');
    if (!svg) return;
    if (!svg.getAttribute('viewBox')) {
      svg.setAttribute('viewBox', '0 0 240 180');
    }
    svg.removeAttribute('width');
    svg.removeAttribute('height');
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('focusable', 'false');
  }

  function parseSvgViewBox(svg) {
    var raw = String(svg.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
    if (raw.length === 4 && raw.every(function (value) { return Number.isFinite(value); }) && raw[2] > 0 && raw[3] > 0) {
      return raw;
    }
    return [0, 0, 240, 180];
  }

  function isFullCanvasRect(node, viewBox) {
    if (!node || String(node.tagName || '').toLowerCase() !== 'rect') return false;
    var x = Number(node.getAttribute('x') || viewBox[0] || 0);
    var y = Number(node.getAttribute('y') || viewBox[1] || 0);
    var width = Number(node.getAttribute('width') || 0);
    var height = Number(node.getAttribute('height') || 0);
    return Math.abs(x - viewBox[0]) <= 1
      && Math.abs(y - viewBox[1]) <= 1
      && width >= viewBox[2] * 0.95
      && height >= viewBox[3] * 0.95;
  }

  function fitAiDrawingSvgToContent() {
    var svg = els.aiDrawing.querySelector('svg');
    if (!svg || !svg.getBBox) return;
    var viewBox = parseSvgViewBox(svg);
    var viewBoxRatio = viewBox[2] / viewBox[3];
    var allowed = { g: true, path: true, line: true, polyline: true, polygon: true, rect: true, circle: true, ellipse: true };
    var items = Array.prototype.slice.call(svg.children).filter(function (node) {
      var tag = String(node.tagName || '').toLowerCase();
      return allowed[tag] && !isFullCanvasRect(node, viewBox);
    });
    var bounds = null;
    items.forEach(function (node) {
      try {
        var box = node.getBBox();
        if (!box || (!box.width && !box.height)) return;
        if (!bounds) {
          bounds = { x1: box.x, y1: box.y, x2: box.x + box.width, y2: box.y + box.height };
          return;
        }
        bounds.x1 = Math.min(bounds.x1, box.x);
        bounds.y1 = Math.min(bounds.y1, box.y);
        bounds.x2 = Math.max(bounds.x2, box.x + box.width);
        bounds.y2 = Math.max(bounds.y2, box.y + box.height);
      } catch (_) {}
    });
    if (!bounds) return;
    var contentWidth = Math.max(1, bounds.x2 - bounds.x1);
    var contentHeight = Math.max(1, bounds.y2 - bounds.y1);
    var centerX = (bounds.x1 + bounds.x2) / 2;
    var centerY = (bounds.y1 + bounds.y2) / 2;
    var maxContentRatio = 0.68;
    var nextWidth = Math.max(viewBox[2], contentWidth / maxContentRatio, (contentHeight / maxContentRatio) * viewBoxRatio);
    var nextHeight = nextWidth / viewBoxRatio;
    if (nextHeight < viewBox[3]) {
      nextHeight = viewBox[3];
      nextWidth = nextHeight * viewBoxRatio;
    }
    var nextX = centerX - nextWidth / 2;
    var nextY = centerY - nextHeight / 2;
    svg.setAttribute('viewBox', [nextX, nextY, nextWidth, nextHeight].map(function (value) {
      return Number(value.toFixed(2));
    }).join(' '));
  }

  function animateAiDrawing() {
    var svg = els.aiDrawing.querySelector('svg');
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
      node.style.transformOrigin = 'center';
      node.style.transition = 'opacity 180ms ease ' + (index * 45) + 'ms, transform 180ms ease ' + (index * 45) + 'ms';
      node.style.transform = 'scale(0.985)';
    });
    requestAnimationFrame(function () {
      items.forEach(function (node) {
        node.style.opacity = '1';
        node.style.transform = 'scale(1)';
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

  function startHeartbeat() {
    clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = setInterval(function () {
      if (!state.routeActive) return;
      var visible = !document.hidden;
      post(ROUTE_API + '/route/heartbeat', routePayload({
        visible: visible,
        pageVisible: visible,
        visibilityState: document.visibilityState || (visible ? 'visible' : 'hidden')
      }), 5000).then(function (res) {
        if (res && res.active === false) {
          state.routeActive = false;
          clearInterval(state.heartbeatTimer);
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
      if (res.state && res.state.lanlan_name) state.lanlanName = String(res.state.lanlan_name || state.lanlanName);
      setStatus('active', 'Active');
      addMessage('drawingGuess.messages.routeActive', 'Game route is active for {{name}}.', { name: state.lanlanName });
      startHeartbeat();
      return true;
    }).catch(function () {
      setStatus('failed', 'Start failed');
      addMessage('drawingGuess.messages.startFailed', 'Route start failed: {{reason}}', { reason: 'request_failed' });
      return false;
    }).finally(updateControls);
  }

  function endRoute(useBeacon) {
    stopCountdown();
    if (!state.routeActive && !useBeacon) return Promise.resolve({ ok: true });
    state.routeEnding = true;
    setStatus('ending', 'Ending');
    updateControls();
    clearInterval(state.heartbeatTimer);
    var payload = JSON.stringify(routePayload({
      reason: state.phase === 'summary' ? 'drawing_guess_game_over' : 'drawing_guess_abandoned',
      roundCompleted: state.phase === 'summary',
      round_completed: state.phase === 'summary',
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
      setPhase('ended');
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

  function startGame() {
    readMemoryConsent();
    els.tutorialOverlay.hidden = true;
    startRoute().then(function (ok) {
      if (ok) startRound();
    });
  }

  function startRound() {
    stopCountdown();
    resetCanvas();
    state.aiSvg = '';
    state.userPng = '';
    state.userDrawAnswer = null;
    setPhase('loading_round');
    showPlaceholder();
    setBadge(t('drawingGuess.phases.loading_round', 'Loading'));
    addMessage('drawingGuess.messages.roundStart', 'A new round is starting.');
    return post(ROUND_API + '/round/start', roundPayload(), 10000)
      .then(function (res) {
        if (!res || !res.ok) throw new Error((res && res.reason) || 'round_start_failed');
        setPhase('ai_drawing');
        setBadge(t('drawingGuess.phases.ai_drawing', 'Neko drawing'));
        return post(ROUND_API + '/ai-draw', roundPayload(), 10000);
      })
      .then(function (res) {
        if (!res || !res.ok) throw new Error((res && res.reason) || 'ai_draw_failed');
        state.aiSvg = (res.drawing && res.drawing.svg) || '';
        showAiDrawing(state.aiSvg);
        setPhase('user_guessing');
        setChatPlaceholder('drawingGuess.input.guessPlaceholder', 'Type your guess or ask for a hint');
        addMessage('drawingGuess.messages.aiDrawingReady', 'She finished drawing. Try to guess it.');
        startCountdown(res.guess_seconds || 60, handleGuessTimeout);
      })
      .catch(function (err) {
        setPhase('tutorial');
        showPlaceholder();
        addMessage('drawingGuess.messages.roundFailed', 'Round failed: {{reason}}', { reason: err && err.message ? err.message : 'unknown' });
      })
      .finally(updateControls);
  }

  function handleGuessTimeout() {
    post(ROUND_API + '/timeout', roundPayload(), 10000).then(function (res) {
      if (res && res.ok) {
        addMessage('drawingGuess.messages.guessTimeout', 'Time is up. The answer was {{answer}}.', {
          answer: res.answer ? res.answer.label : ''
        });
        beginUserDrawing(res.user_draw_answer, res.draw_seconds || 60);
      }
    }).catch(function () {});
  }

  function submitUserGuess(text) {
    return post(ROUND_API + '/input', roundPayload({ text: text }), 10000).then(function (res) {
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      addMessage('', res.message || '');
      if (res.correct) {
        stopCountdown();
        addMessage('drawingGuess.messages.answerReveal', 'Answer: {{answer}}', {
          answer: res.answer ? res.answer.label : ''
        });
        beginUserDrawing(res.user_draw_answer, res.draw_seconds || 60);
      }
    }).finally(updateControls);
  }

  function submitGameChat(text) {
    return post(ROUND_API + '/input', roundPayload({ text: text }), 20000).then(function (res) {
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      if (res.message) addMessage('', res.message);
    }).finally(updateControls);
  }

  function submitFeedbackInput(text) {
    return post(ROUND_API + '/input', roundPayload({
      text: text,
      image_data_url: state.userPng
    }), 45000).then(function (res) {
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      if (res.kind === 'ai_guess') {
        var guessLabel = res.guess ? res.guess.label : '';
        addMessage('drawingGuess.messages.aiGuessLine', 'She guessed: {{guess}}', { guess: guessLabel });
        if (res.message) addMessage('', res.message);
        if (res.state && res.state.phase === 'summary') {
          renderSummary(res);
        } else {
          setPhase('ai_guess_feedback');
          setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Chat, or type hint: ... to let her try again');
          addMessage('drawingGuess.messages.aiNeedsHint', 'Chat with her, or give a hint and let her try again.');
        }
        return;
      }
      if (res.message) addMessage('', res.message);
    }).finally(updateControls);
  }

  function beginUserDrawing(answer, seconds) {
    state.userDrawAnswer = answer || null;
    resetCanvas();
    showCanvas();
    setPhase('user_drawing');
    setChatPlaceholder('drawingGuess.input.drawingPlaceholder', 'Chat while drawing');
    addMessage('drawingGuess.messages.userDrawPrompt', 'Your word is: {{answer}}. Draw it for her.', {
      answer: answer ? answer.label : ''
    });
    startCountdown(seconds || 60, function () {
      submitDrawing(false);
    });
  }

  function submitDrawing(manual) {
    if (state.phase !== 'user_drawing') return;
    if (manual && !state.hasDrawn) {
      addMessage('drawingGuess.messages.blankCanvas', 'Give her a few lines first.');
      return;
    }
    stopCountdown();
    state.userPng = els.canvas.toDataURL('image/png');
    setPhase('ai_guessing');
    setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Give her a hint after she guesses');
    addMessage('drawingGuess.messages.aiGuessing', 'She is looking at your drawing...');
    postVisionGuess('');
  }

  function postVisionGuess(userHint) {
    return post(ROUND_API + '/vision-guess', roundPayload({
      image_data_url: state.userPng,
      user_hint: userHint || ''
    }), 45000).then(function (res) {
      if (!res || !res.ok) {
        addMessage('drawingGuess.messages.inputFailed', 'Input failed.');
        return;
      }
      var guessLabel = res.guess ? res.guess.label : '';
      addMessage('drawingGuess.messages.aiGuessLine', 'She guessed: {{guess}}', { guess: guessLabel });
      if (res.message) addMessage('', res.message);
      if (res.state && res.state.phase === 'summary') {
        renderSummary(res);
      } else {
        setPhase('ai_guess_feedback');
        setChatPlaceholder('drawingGuess.input.hintPlaceholder', 'Chat, or type hint: ... to let her try again');
        addMessage('drawingGuess.messages.aiNeedsHint', 'Chat with her, or give a hint and let her try again.');
      }
    }).finally(updateControls);
  }

  function renderSummary(res) {
    stopCountdown();
    setPhase('summary');
    showSummary();
    var scores = (res.state && res.state.scores) || { user: 0, neko: 0 };
    var outcomeKey = scores.user > scores.neko ? 'userWin' : (scores.neko > scores.user ? 'nekoWin' : 'tie');
    var answerLabel = res.answer ? res.answer.label : (state.userDrawAnswer ? state.userDrawAnswer.label : '');
    els.summary.innerHTML = ''
      + '<div>'
      + '<h3>' + escapeHtml(t('drawingGuess.summary.title', 'Round summary')) + '</h3>'
      + '<p>' + escapeHtml(t('drawingGuess.summary.outcome.' + outcomeKey, 'Nice round.')) + '</p>'
      + '<p>' + escapeHtml(t('drawingGuess.summary.score', 'You {{user}} - {{neko}} Neko', { user: scores.user, neko: scores.neko })) + '</p>'
      + '<p>' + escapeHtml(t('drawingGuess.summary.userDrawAnswer', 'Your drawing answer: {{answer}}', { answer: answerLabel })) + '</p>'
      + '</div>'
      + '<div class="dg-summary-grid">'
      + '<section class="dg-thumb"><h4>' + escapeHtml(t('drawingGuess.summary.nekoArt', 'Neko drawing')) + '</h4><div id="summary-ai-art" class="dg-thumb-preview"></div><button id="download-ai-svg" class="dg-button" type="button">' + escapeHtml(t('drawingGuess.actions.downloadSvg', 'Download SVG')) + '</button></section>'
      + '<section class="dg-thumb"><h4>' + escapeHtml(t('drawingGuess.summary.userArt', 'Your drawing')) + '</h4><div class="dg-thumb-preview"><img alt="" src="' + escapeAttr(state.userPng) + '"></div><button id="download-user-png" class="dg-button" type="button">' + escapeHtml(t('drawingGuess.actions.downloadPng', 'Download PNG')) + '</button></section>'
      + '</div>';
    $('summary-ai-art').innerHTML = state.aiSvg || '';
    $('download-ai-svg').addEventListener('click', downloadAiSvg);
    $('download-user-png').addEventListener('click', downloadUserPng);
    addMessage('drawingGuess.messages.summaryReady', 'Round summary is ready.');
    updateControls();
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

  function readMemoryConsent() {
    var selected = document.querySelector('input[name="memory-consent"]:checked');
    state.memoryConsent = selected ? selected.value : 'none';
    updateControls();
  }

  function loadCurrentCharacter() {
    if (state.lanlanName) {
      setStatus('ready', 'Ready');
      updateControls();
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
      updateControls();
      return name;
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

  function beginStroke(event) {
    if (state.phase !== 'user_drawing') return;
    event.preventDefault();
    state.isDrawing = true;
    var p = canvasPoint(event);
    els.ctx.beginPath();
    els.ctx.moveTo(p.x, p.y);
    try { els.canvas.setPointerCapture(event.pointerId); } catch (_) {}
  }

  function moveStroke(event) {
    if (!state.isDrawing || state.phase !== 'user_drawing') return;
    event.preventDefault();
    var p = canvasPoint(event);
    els.ctx.lineCap = 'round';
    els.ctx.lineJoin = 'round';
    els.ctx.lineWidth = Number(els.brushSize.value || 7);
    if (state.brushMode === 'eraser') {
      els.ctx.globalCompositeOperation = 'destination-out';
      els.ctx.strokeStyle = 'rgba(0,0,0,1)';
      els.ctx.lineWidth = Number(els.brushSize.value || 7) * 1.8;
    } else {
      els.ctx.globalCompositeOperation = 'source-over';
      els.ctx.strokeStyle = els.brushColor.value || '#24303a';
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
  }

  function handleChatSubmit(event) {
    event.preventDefault();
    var value = String(els.chatInput.value || '').trim();
    if (!value) return;
    els.chatInput.value = '';
    if (state.phase === 'user_guessing') {
      submitUserGuess(value);
    } else if (state.phase === 'user_drawing') {
      submitGameChat(value);
    } else if (state.phase === 'ai_guess_feedback') {
      submitFeedbackInput(value);
    }
  }

  function bindEvents() {
    els.startButton.addEventListener('click', startGame);
    els.tutorialStartButton.addEventListener('click', startGame);
    els.nextRoundButton.addEventListener('click', startRound);
    els.endButton.addEventListener('click', function () { endRoute(false); });
    els.doneButton.addEventListener('click', function () { submitDrawing(true); });
    els.clearCanvasButton.addEventListener('click', function () {
      if (state.phase === 'user_drawing') {
        resetCanvas();
        addMessage('drawingGuess.messages.canvasCleared', 'Canvas cleared.');
      }
    });
    els.reloadCharacterButton.addEventListener('click', function () {
      state.lanlanName = '';
      loadCurrentCharacter();
    });
    els.chatForm.addEventListener('submit', handleChatSubmit);
    els.brushTool.addEventListener('click', function () { setTool('brush'); });
    els.eraserTool.addEventListener('click', function () { setTool('eraser'); });
    els.undoTool.addEventListener('click', undoCanvas);
    els.redoTool.addEventListener('click', redoCanvas);
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
    });
  }

  function init() {
    initEls();
    state.sessionId = String(boot.sessionId || '') || makeSessionId();
    state.lanlanName = String(boot.lanlanName || (window.lanlan_config && window.lanlan_config.lanlan_name) || '').trim();
    resetCanvas();
    showPlaceholder();
    setPhase('tutorial');
    readMemoryConsent();
    bindEvents();
    loadCurrentCharacter();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
