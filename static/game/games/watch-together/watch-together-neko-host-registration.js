/**
 * Host-owned watch-together capability providers consumed by the trusted bootstrap.
 * This file runs before game code and does not expose a registration producer.
 */
(() => {
  'use strict';

  const launchNode = window.document?.getElementById?.('neko-minigame-host-launch');
  if (!launchNode) {
    throw new Error('watch-together host launch registration is missing');
  }

  const providers = Object.freeze({
    'watch-together': Object.freeze({
      // host.mjs defines the page-owned Avatar host before it creates the adapter.
      avatarHostFactory(options) {
        return window.createWatchTogetherAvatarHost(options);
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
