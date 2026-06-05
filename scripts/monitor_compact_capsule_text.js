(function monitorCompactCapsuleText() {
  const startedAt = Date.now();
  const selector = '.compact-chat-capsule-text';
  const state = {
    lastText: undefined,
    lastScrollLeft: undefined,
    lastScrollWidth: undefined,
    lastMessageSummary: undefined,
    timer: 0,
    observer: null
  };

  function nowMs() {
    return String(Date.now() - startedAt).padStart(6, ' ');
  }

  function getLatestGuideMessageSummary() {
    const host = window.reactChatWindowHost;
    if (!host || typeof host.getState !== 'function') {
      return '';
    }

    try {
      const snapshot = host.getState();
      const messages = snapshot && Array.isArray(snapshot.messages) ? snapshot.messages : [];
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        if (!message || typeof message.id !== 'string' || !message.id.startsWith('yui-guide-')) {
          continue;
        }

        const blocks = Array.isArray(message.blocks) ? message.blocks : [];
        const text = blocks
          .filter(block => block && block.type === 'text' && typeof block.text === 'string')
          .map(block => block.text)
          .join(' ');
        return [
          'id=' + message.id,
          'status=' + String(message.status || ''),
          'len=' + Array.from(text).length,
          'text=' + JSON.stringify(text.slice(0, 48))
        ].join(' ');
      }
    } catch (error) {
      return 'host_state_error=' + (error && error.message ? error.message : String(error));
    }

    return 'no-guide-message';
  }

  function readCapsule() {
    const node = document.querySelector(selector);
    const text = node ? node.textContent || '' : '';
    const scrollLeft = node ? Math.round(node.scrollLeft) : -1;
    const scrollWidth = node ? Math.round(node.scrollWidth) : -1;
    const clientWidth = node ? Math.round(node.clientWidth) : -1;
    const streaming = node ? node.getAttribute('data-compact-preview-streaming') : '';
    const scrollable = node ? node.getAttribute('data-compact-preview-scrollable') : '';
    const messageSummary = getLatestGuideMessageSummary();

    return {
      found: !!node,
      text,
      length: Array.from(text).length,
      scrollLeft,
      scrollWidth,
      clientWidth,
      streaming,
      scrollable,
      messageSummary
    };
  }

  function log(reason) {
    const snapshot = readCapsule();
    const changed = (
      snapshot.text !== state.lastText
      || snapshot.scrollLeft !== state.lastScrollLeft
      || snapshot.scrollWidth !== state.lastScrollWidth
      || snapshot.messageSummary !== state.lastMessageSummary
    );
    if (!changed && reason !== 'start') {
      return;
    }

    state.lastText = snapshot.text;
    state.lastScrollLeft = snapshot.scrollLeft;
    state.lastScrollWidth = snapshot.scrollWidth;
    state.lastMessageSummary = snapshot.messageSummary;

    console.log(
      '[capsule-monitor +' + nowMs() + 'ms]',
      reason,
      'found=' + snapshot.found,
      'len=' + snapshot.length,
      'scroll=' + snapshot.scrollLeft + '/' + snapshot.scrollWidth + '/' + snapshot.clientWidth,
      'streaming=' + snapshot.streaming,
      'scrollable=' + snapshot.scrollable,
      'text=' + JSON.stringify(snapshot.text),
      'latestGuide={' + snapshot.messageSummary + '}'
    );
  }

  function attachObserver() {
    if (state.observer) {
      state.observer.disconnect();
      state.observer = null;
    }

    const node = document.querySelector(selector);
    if (!node || typeof MutationObserver === 'undefined') {
      return;
    }

    state.observer = new MutationObserver(() => log('mutation'));
    state.observer.observe(node, {
      childList: true,
      characterData: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-compact-preview-streaming', 'data-compact-preview-scrollable']
    });
  }

  if (window.__compactCapsuleTextMonitorStop) {
    window.__compactCapsuleTextMonitorStop();
  }

  log('start');
  attachObserver();

  state.timer = window.setInterval(() => {
    attachObserver();
    log('poll');
  }, 80);

  window.__compactCapsuleTextMonitorStop = function stopCompactCapsuleTextMonitor() {
    if (state.timer) {
      window.clearInterval(state.timer);
      state.timer = 0;
    }
    if (state.observer) {
      state.observer.disconnect();
      state.observer = null;
    }
    console.log('[capsule-monitor +' + nowMs() + 'ms] stopped');
  };

  console.log('[capsule-monitor] running. Stop with window.__compactCapsuleTextMonitorStop()');
})();
