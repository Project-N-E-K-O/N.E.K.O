/**
 * Soccer compatibility factory for the shared trusted same-origin mini-game host.
 */
(() => {
  'use strict';

  if (typeof window.createNekoMiniGameSameOriginHost !== 'function') {
    throw new Error('neko-minigame-same-origin-host.js must load before soccer-neko-adapter.js');
  }

  window.createSoccerNekoAdapter = function createSoccerNekoAdapter(options = {}) {
    return window.createNekoMiniGameSameOriginHost({
      gameType: 'soccer',
      gameVersion: '1.0.0',
      source: 'soccer_demo',
      displayName: 'Soccer',
      ...options,
    });
  };
  window.SoccerNekoHostError = window.NekoMiniGameHostError;
})();
