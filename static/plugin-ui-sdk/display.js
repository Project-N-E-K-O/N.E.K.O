/* Shared display client. Hosted embedding selects factory-only mode explicitly. */
(function (factory) {
  if (typeof module === 'object' && module && module.exports &&
      (typeof window === 'undefined' || module.nekoPluginDisplayFactoryOnly)) {
    module.exports = factory;
  } else if (!window.NekoPluginDisplay) {
    window.NekoPluginDisplay = factory({ origin: window.location.origin });
  }
})(function createPluginDisplayClient(options) {
  'use strict';
  const origin = options.origin;
  const reportError = options.reportError || ((scope, error) => console.error('[plugin-display]', scope, error));
  const documentId = 'display-document-' + Date.now() + '-' + Math.random().toString(36).slice(2);
  let shown = false;
  let ready = false;
  let connectionId = null;
  let displayMode = 'normal';
  let escapeExit = null;
  const requests = new Map();
  const listeners = new Set();
  function publish(state) {
    displayMode = state.mode;
    for (const listener of listeners) {
      try { listener(state); } catch (error) { reportError('display.state', error); }
    }
  }
  function send(pending) {
    pending.sent = true;
    pending.message.connectionId = connectionId;
    window.parent.postMessage(pending.message, origin);
  }
  function cancelRequests(sentOnly) {
    for (const [requestId, pending] of requests) {
      if (sentOnly && !pending.sent) continue;
      requests.delete(requestId);
      if (pending.timer !== null) clearTimeout(pending.timer);
      if (pending.message.method === 'display.getState') pending.reject(new DOMException('Plugin display connection was disposed', 'AbortError'));
      else pending.resolve({ status: 'cancelled', state: { mode: 'normal', supportedModes: [] } });
    }
  }
  function request(method, payload) {
    if (window.parent === window) {
      const state = { mode: 'normal', supportedModes: [] };
      return Promise.resolve(method === 'display.getState' ? state : { status: 'unsupported', state });
    }
    const requestId = 'display-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    return new Promise((resolve, reject) => {
      const timer = method === 'display.requestMode' ? null : setTimeout(() => {
        requests.delete(requestId);
        reject(new Error('Plugin display host did not respond'));
      }, 30000);
      const pending = { resolve, reject, timer, message: { type: 'neko-hosted-surface-request', documentId, requestId, method, payload }, sent: false };
      requests.set(requestId, pending);
      if (ready) send(pending);
    });
  }
  function announceDocument() {
    if (!shown || window.parent === window) return;
    window.parent.postMessage({ type: 'neko-plugin-display-ready', documentId, connectionId }, origin);
  }
  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== origin) return;
    const data = event.data;
    if (!data || typeof data !== 'object') return;
    if (data.type === 'neko-plugin-display-connect' && typeof data.connectionId === 'string') {
      if (connectionId !== data.connectionId) {
        ready = false;
        cancelRequests(true);
        connectionId = data.connectionId;
      }
      announceDocument();
      return;
    }
    if (data.documentId !== documentId || data.connectionId !== connectionId) return;
    if (data.type === 'neko-plugin-display-connected') {
      ready = true;
      for (const pending of requests.values()) if (!pending.sent) send(pending);
      publish(data.state);
      return;
    }
    if (data.type === 'neko-plugin-display-state') { publish(data.state); return; }
    if (data.type !== 'neko-hosted-surface-response') return;
    const pending = requests.get(data.requestId);
    if (!pending) return;
    requests.delete(data.requestId);
    if (pending.timer !== null) clearTimeout(pending.timer);
    if (data.ok) {
      displayMode = (data.result.state || data.result).mode;
      pending.resolve(data.result);
    } else pending.reject(new Error(data.error || 'Plugin display request failed'));
  });
  function showDocument() {
    shown = true;
    ready = false;
    announceDocument();
  }
  window.addEventListener('pageshow', showDocument);
  // Iframe keyboard events do not bubble to the host. One held Escape must
  // not enqueue a second exit behind a new interactive request.
  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || displayMode !== 'focus' || escapeExit) return;
    const keyConnection = connectionId;
    // Native keyboard dispatch can run microtasks between window listeners.
    // Let the entire event finish so a later plugin modal can consume Escape.
    escapeExit = new Promise(resolve => {
      window.setTimeout(() => {
        if (event.defaultPrevented || displayMode !== 'focus' || !ready || connectionId !== keyConnection) { resolve(); return; }
        request('display.exit', { reason: 'escape' }).then(resolve, error => {
          reportError('display.exit', error);
          resolve();
        });
      }, 0);
    }).finally(() => { escapeExit = null; });
  });
  if (document.readyState === 'complete') showDocument();
  window.addEventListener('pagehide', () => {
    shown = false;
    ready = false;
    window.parent.postMessage({ type: 'neko-plugin-display-dispose', documentId, connectionId }, origin);
    cancelRequests(false);
  });
  return {
    getState: () => request('display.getState', {}),
    requestMode: mode => request('display.requestMode', { mode }),
    exit: () => request('display.exit', {}),
    onStateChange(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
});
