/**
 * Host-owned air-basketball capability providers consumed by the trusted bootstrap.
 * This file runs before game code and does not expose a registration producer.
 */
(() => {
  'use strict';

  const launchNode = window.document?.getElementById?.('neko-minigame-host-launch');
  if (!launchNode) {
    throw new Error('air-basketball host launch registration is missing');
  }

  const providers = Object.freeze({
    'air-basketball': Object.freeze({
      // sdk-bootstrap.js defines the page-owned Avatar host before it creates the adapter.
      avatarHostFactory(options) {
        return window.createAirBasketballAvatarHost(options);
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
