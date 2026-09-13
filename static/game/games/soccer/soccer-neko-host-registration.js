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

  const providers = Object.freeze({
    soccer: Object.freeze({
      quickLines(payload, options = {}) {
        return window.fetch('/api/game/soccer/quick-lines', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          credentials: 'same-origin',
          signal: options.signal,
        });
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
