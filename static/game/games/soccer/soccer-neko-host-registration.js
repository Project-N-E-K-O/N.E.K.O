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
  async function getSoccerRegMutationHeaders(forceRefresh) {
    try {
      const security = window.nekoLocalMutationSecurity;
      if (security && typeof security.getMutationHeaders === 'function') {
        if (forceRefresh && typeof security.refreshToken === 'function') {
          await security.refreshToken();
        }
        const headers = await security.getMutationHeaders();
        if (headers && typeof headers === 'object') {
          return headers;
        }
      }
    } catch (_) { /* fall back to page_config */ }
    if (forceRefresh) _soccerRegCsrfToken = '';
    if (_soccerRegCsrfToken) {
      return { 'X-CSRF-Token': _soccerRegCsrfToken };
    }
    try {
      const resp = await window.fetch('/api/config/page_config', { cache: 'no-store', credentials: 'same-origin' });
      const cfg = resp && resp.ok ? await resp.json() : null;
      const token = cfg && cfg.autostart_csrf_token;
      if (typeof token === 'string' && token) {
        _soccerRegCsrfToken = token;
        return { 'X-CSRF-Token': token };
      }
    } catch (_) { /* guarded POST will surface rejection */ }
    return {};
  }

  const providers = Object.freeze({
    soccer: Object.freeze({
      avatarHostFactory(options) {
        return window.createSoccerAvatarHost(options);
      },
      async quickLines(payload, options = {}) {
        const bodyJson = JSON.stringify(payload);
        async function sendOnce(forceRefresh) {
          const csrfHeaders = await getSoccerRegMutationHeaders(forceRefresh);
          return window.fetch('/api/game/soccer/quick-lines', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...csrfHeaders },
            body: bodyJson,
            credentials: 'same-origin',
            signal: options.signal,
          });
        }
        const response = await sendOnce(false);
        if (response.status !== 403 || typeof response.clone !== 'function') return response;
        try {
          const errPayload = await response.clone().json();
          if (!errPayload || errPayload.error_code !== 'csrf_validation_failed') return response;
        } catch (_) {
          return response;
        }
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
