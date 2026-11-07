/* Shared display client. Classic scripts install the static API; the hosted
 * bundle imports the same factory through its local CommonJS wrapper. */
(function (factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory;
  } else if (!window.NekoPluginDisplay) {
    window.NekoPluginDisplay = factory({ origin: window.location.origin });
  }
})(function createPluginDisplayClient(options) {
  'use strict';
  const origin = options.origin;
  const reportError = options.reportError || ((scope, error) => console.error('[plugin-display]', scope, error));
  const documentId = `display-document-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  let ready = false;
  let displayMode = 'normal';
  const requests = new Map();
  const listeners = new Set();
  function request(method, payload) {
    if (window.parent === window) {
      const state = { mode: 'normal', supportedModes: [] };
      return Promise.resolve(method === 'display.getState' ? state : { status: 'unsupported', state });
    }
    const requestId = `display-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return new Promise((resolve, reject) => {
      const timer = method === 'display.requestMode' ? null : setTimeout(() => {
        requests.delete(requestId);
        reject(new Error('Plugin display host did not respond'));
      }, 30000);
      const pending = { resolve, reject, timer, message: { type: 'neko-hosted-surface-request', documentId, requestId, method, payload }, sent: ready };
      requests.set(requestId, pending);
      if (ready) window.parent.postMessage(pending.message, origin);
    });
  }
  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== origin) return;
    const data = event.data;
    if (!data || typeof data !== 'object' || data.documentId !== documentId) return;
    if (data.type === 'neko-plugin-display-state') {
      displayMode = data.state.mode;
      for (const listener of listeners) {
        try { listener(data.state); } catch (error) { reportError('display.state', error); }
      }
      return;
    }
    if (data.type !== 'neko-hosted-surface-response') return;
    const pending = requests.get(data.requestId);
    if (!pending) return;
    requests.delete(data.requestId);
    if (pending.timer !== null) clearTimeout(pending.timer);
    if (data.ok) {
      displayMode = (data.result.state || data.result).mode;
      pending.resolve(data.result);
    }
    else pending.reject(new Error(data.error || 'Plugin display request failed'));
  });
  function announceDocument() {
    ready = true;
    if (window.parent === window) return;
    window.parent.postMessage({ type: 'neko-plugin-display-ready', documentId }, origin);
    for (const pending of requests.values()) {
      if (!pending.sent) { pending.sent = true; window.parent.postMessage(pending.message, origin); }
    }
  }
  // Parent iframe load resets the old document before child pageshow runs.
  // Queue early component/script requests until that boundary has passed.
  window.addEventListener('pageshow', announceDocument);
  // Keyboard events inside an iframe do not bubble to the host page.
  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    queueMicrotask(() => {
      if (!event.defaultPrevented && displayMode === 'focus') {
        request('display.exit', {}).catch(error => reportError('display.exit', error));
      }
    });
  });
  if (document.readyState === 'complete') announceDocument();
  window.addEventListener('pagehide', () => {
    ready = false;
    window.parent.postMessage({ type: 'neko-plugin-display-dispose', documentId }, origin);
    for (const [requestId, pending] of requests) {
      window.parent.postMessage({ type: 'neko-hosted-surface-cancel', documentId, requestId }, origin);
      if (pending.timer !== null) clearTimeout(pending.timer);
      if (pending.message.method === 'display.getState') pending.reject(new DOMException('Plugin document was disposed', 'AbortError'));
      else pending.resolve({ status: 'cancelled', state: { mode: 'normal', supportedModes: [] } });
    }
    requests.clear();
  });
  return {
    getState: () => request('display.getState', {}),
    requestMode: mode => request('display.requestMode', { mode }),
    exit: () => request('display.exit', {}),
    onStateChange(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
});
