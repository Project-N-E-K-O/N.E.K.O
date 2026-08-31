/**
 * Trusted page-host bootstrap for the first-phase same-origin mini-game host.
 *
 * The trusted page template must emit a JSON script with id
 * `neko-minigame-host-launch` immediately before loading this file. This file
 * consumes and removes that host-owned node synchronously, installs the
 * bounded immutable script-scoped handoff, and loads the internal adapter
 * before game code. The adapter script node is removed after load so the
 * non-configurable handoff is released with its DOM owner.
 */
(() => {
  'use strict';

  const REGISTRATION_LIMIT = 64;
  const CAPABILITY_LIMIT = 32;
  const DEFAULT_ADAPTER_URL = '/static/game/sdk/neko-minigame-same-origin-host.js';

  function normalizeRegistrations(value, providerRegistry = {}) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return Object.freeze({});
    const result = {};
    for (const [rawKey, rawRegistration] of Object.entries(value)) {
      if (Object.keys(result).length >= REGISTRATION_LIMIT) break;
      if (!rawRegistration || typeof rawRegistration !== 'object' || Array.isArray(rawRegistration)) continue;
      const gameId = String(rawRegistration.gameId || '').trim();
      const version = String(rawRegistration.version || '').trim();
      const mode = String(rawRegistration.mode || '').trim();
      if (
        !gameId
        || gameId !== String(rawKey || '').trim()
        || gameId.length > 128
        || !version
        || version.length > 64
        || !['registered', 'development'].includes(mode)
      ) continue;
      const allowedCapabilities = Object.freeze([
        ...new Set(
          (Array.isArray(rawRegistration.allowedCapabilities)
            ? rawRegistration.allowedCapabilities
            : [])
            .map((name) => String(name || '').trim())
            .filter((name) => Boolean(name) && name.length <= 64),
        ),
      ].slice(0, CAPABILITY_LIMIT));
      const rawProviders = providerRegistry?.[gameId];
      const capabilityProviders = Object.freeze({
        quickLines: typeof rawProviders?.quickLines === 'function'
          ? rawProviders.quickLines
          : null,
      });
      result[gameId] = Object.freeze({
        mode,
        gameId,
        publisherId: String(rawRegistration.publisherId || '').trim().slice(0, 128),
        version,
        allowedCapabilities,
        capabilityProviders,
      });
    }
    return Object.freeze(result);
  }

  function loadAdapterScript(adapterUrl, documentImpl, registrations) {
    return new Promise((resolve, reject) => {
      if (!documentImpl?.createElement || !documentImpl?.head?.appendChild) {
        reject(new Error('MINIGAME_HOST_DOCUMENT_UNAVAILABLE'));
        return;
      }
      const script = documentImpl.createElement('script');
      script.src = adapterUrl;
      script.async = false;
      Object.defineProperty(script, 'nekoHostLaunchRegistry', {
        value: registrations,
        writable: false,
        configurable: false,
      });
      const releaseLaunchBinding = () => {
        script.onload = null;
        script.onerror = null;
        try { script.remove?.(); }
        catch (_) { /* the adapter script is already detached */ }
      };
      script.onload = () => {
        releaseLaunchBinding();
        resolve();
      };
      script.onerror = () => {
        releaseLaunchBinding();
        reject(new Error('MINIGAME_HOST_ADAPTER_LOAD_FAILED'));
      };
      documentImpl.head.appendChild(script);
    });
  }

  const documentImpl = window.document;
  const launchNode = documentImpl?.getElementById?.('neko-minigame-host-launch');
  let launchConfig = {};
  try {
    launchConfig = JSON.parse(String(launchNode?.textContent || '{}'));
  } catch (_) {
    launchConfig = {};
  }
  try { launchNode?.remove?.(); } catch (_) { /* already detached */ }
  const registrations = normalizeRegistrations(
    launchConfig.registrations,
    launchNode?.nekoCapabilityProviders,
  );
  const adapterUrl = String(launchConfig.adapterUrl || DEFAULT_ADAPTER_URL);

  window.nekoMiniGameSameOriginHostReady = (async () => {
      await loadAdapterScript(adapterUrl, documentImpl, registrations);
      if (typeof window.createNekoMiniGameSameOriginHost !== 'function') {
        throw new Error('MINIGAME_HOST_ADAPTER_FACTORY_MISSING');
      }
      return window.createNekoMiniGameSameOriginHost;
    })();
})();
