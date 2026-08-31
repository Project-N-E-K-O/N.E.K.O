/**
 * Soccer compatibility factory for the shared trusted same-origin mini-game host.
 */
(() => {
  'use strict';

  window.createSoccerNekoAdapter = async function createSoccerNekoAdapter(options = {}) {
    await window.nekoMiniGameSameOriginHostReady;
    if (typeof window.createNekoMiniGameSameOriginHost !== 'function') {
      throw new Error('neko-minigame same-origin host bootstrap did not install its factory');
    }
    const host = window.createNekoMiniGameSameOriginHost({
      gameType: 'soccer',
      gameVersion: '1.0.0',
      source: 'soccer_demo',
      displayName: 'Soccer',
      ...options,
    });
    Object.defineProperty(host, 'evaluatePassiveGuard', {
      configurable: false,
      enumerable: false,
      writable: false,
      value(payload = {}, requestOptions = {}) {
        return host._post(
          host._gameEndpoint('passive-guard'),
          host._trustedRuntimePayload(payload),
          {
            operation: 'soccer_passive_guard',
            timeoutMs: 9000,
            ...requestOptions,
          },
        );
      },
    });
    window.SoccerNekoHostError = window.NekoMiniGameHostError;
    return host;
  };
})();
