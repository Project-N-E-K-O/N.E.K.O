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
    Object.defineProperty(host, 'migrateLegacySettings', {
      value: async function migrateLegacySettings(game) {
        if (!game.capabilities.has('storage')) return;
        // Fixed, soccer-only compatibility keys. Never erase the old value,
        // and never replace a preference already saved through the SDK.
        for (const [legacyKey, key, parse] of [
          ['neko.soccerGameAudio.voiceMix', 'settings/voice-mix-percent',
            (raw) => raw.trim() && Number.isFinite(Number(raw))
              ? Math.round(Math.max(0, Math.min(100, Number(raw)))) : undefined],
          ['neko.soccer.surrenderReminderEnabled', 'settings/surrender-reminder-enabled',
            (raw) => raw === 'true' ? true : raw === 'false' ? false : undefined],
        ]) {
          try {
            const current = await game.storage.get(key);
            if (!current.ok || current.data?.found !== false) continue;
            const raw = window.localStorage?.getItem(legacyKey);
            if (raw == null) continue;
            const value = parse(raw);
            if (value !== undefined) await game.storage.set(key, value);
          } catch (_) { /* optional storage: retain defaults and retry next page load */ }
        }
      },
    });
    return host;
  };
})();
