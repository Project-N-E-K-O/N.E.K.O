/**
 * Host-owned soccer capability providers consumed by the trusted bootstrap.
 * This file runs before game code and does not expose a registration producer.
 */
(() => {
  'use strict';

  const launchNode = window.document?.getElementById?.('neko-minigame-host-launch');
  if (!launchNode) {
    throw new Error('soccer host launch registration is missing');
  }

  let _soccerRegCsrfToken = '';
  function createAbortError() {
    const err = new Error('The operation was aborted.');
    err.name = 'AbortError';
    return err;
  }

  function isAbortError(err) {
    return Boolean(err && (err.name === 'AbortError' || err.name === 'TimeoutError'));
  }

  function awaitWithSignal(promise, signal) {
    const wrapped = Promise.resolve(promise);
    if (!signal) return wrapped;
    if (signal.aborted) {
      wrapped.catch(() => {});
      return Promise.reject(createAbortError());
    }
    return new Promise((resolve, reject) => {
      let settled = false;
      function cleanup() {
        if (typeof signal.removeEventListener === 'function') {
          signal.removeEventListener('abort', onAbort);
        }
      }
      function onAbort() {
        if (settled) return;
        settled = true;
        cleanup();
        reject(createAbortError());
      }
      if (typeof signal.addEventListener === 'function') {
        signal.addEventListener('abort', onAbort, { once: true });
      }
      wrapped.then(
        (value) => {
          if (settled) return;
          settled = true;
          cleanup();
          resolve(value);
        },
        (error) => {
          if (settled) return;
          settled = true;
          cleanup();
          reject(error);
        },
      );
    });
  }

  async function getSoccerRegMutationHeaders(forceRefresh, signal) {
    if (signal?.aborted) throw createAbortError();
    try {
      const security = window.nekoLocalMutationSecurity;
      if (security && typeof security.getMutationHeaders === 'function') {
        if (forceRefresh && typeof security.refreshToken === 'function') {
          await awaitWithSignal(security.refreshToken(), signal);
          if (signal?.aborted) throw createAbortError();
        }
        const headers = await awaitWithSignal(security.getMutationHeaders(), signal);
        if (signal?.aborted) throw createAbortError();
        if (headers && typeof headers === 'object') {
          return headers;
        }
      }
    } catch (err) {
      if (signal?.aborted || isAbortError(err)) {
        throw isAbortError(err) ? err : createAbortError();
      }
      /* fall back to page_config */
    }
    if (signal?.aborted) throw createAbortError();
    if (forceRefresh) _soccerRegCsrfToken = '';
    if (_soccerRegCsrfToken) {
      return { 'X-CSRF-Token': _soccerRegCsrfToken };
    }
    try {
      const fetchInit = { cache: 'no-store', credentials: 'same-origin' };
      if (signal) fetchInit.signal = signal;
      const resp = await awaitWithSignal(
        window.fetch('/api/config/page_config', fetchInit),
        signal,
      );
      if (signal?.aborted) throw createAbortError();
      const cfg = resp && resp.ok ? await awaitWithSignal(resp.json(), signal) : null;
      if (signal?.aborted) throw createAbortError();
      const token = cfg && cfg.autostart_csrf_token;
      if (typeof token === 'string' && token) {
        _soccerRegCsrfToken = token;
        return { 'X-CSRF-Token': token };
      }
    } catch (err) {
      if (signal?.aborted || isAbortError(err)) {
        throw isAbortError(err) ? err : createAbortError();
      }
      /* guarded POST will surface rejection */
    }
    if (signal?.aborted) throw createAbortError();
    return {};
  }

  const providers = Object.freeze({
    soccer: Object.freeze({
      avatarHostFactory(options) {
        return window.createSoccerAvatarHost(options);
      },
      async quickLines(payload, options = {}) {
        const signal = options && options.signal;
        if (signal?.aborted) throw createAbortError();
        const bodyJson = JSON.stringify(payload);
        async function sendOnce(forceRefresh) {
          if (signal?.aborted) throw createAbortError();
          const csrfHeaders = await getSoccerRegMutationHeaders(forceRefresh, signal);
          if (signal?.aborted) throw createAbortError();
          const fetchInit = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...csrfHeaders },
            body: bodyJson,
            credentials: 'same-origin',
          };
          if (signal) fetchInit.signal = signal;
          return awaitWithSignal(
            window.fetch('/api/game/soccer/quick-lines', fetchInit),
            signal,
          );
        }
        const response = await sendOnce(false);
        if (signal?.aborted) throw createAbortError();
        if (!response || response.status !== 403 || typeof response.clone !== 'function') {
          return response;
        }
        try {
          const errPayload = await awaitWithSignal(response.clone().json(), signal);
          if (signal?.aborted) throw createAbortError();
          if (!errPayload || errPayload.error_code !== 'csrf_validation_failed') return response;
        } catch (err) {
          if (signal?.aborted || isAbortError(err)) {
            throw isAbortError(err) ? err : createAbortError();
          }
          return response;
        }
        if (signal?.aborted) throw createAbortError();
        return sendOnce(true);
      },
    }),
  });

  Object.defineProperty(launchNode, 'nekoCapabilityProviders', {
    value: providers,
    configurable: false,
    enumerable: false,
    writable: false,
  });
})();
