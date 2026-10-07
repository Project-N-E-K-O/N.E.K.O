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
  const COMMAND_ROUTE_LIMIT = 64;
  const DEFAULT_COMMAND_REQUEST_BYTES = 256 * 1024;
  const MAX_COMMAND_REQUEST_BYTES = 2 * 1024 * 1024;
  const DEFAULT_COMMAND_TIMEOUT_MS = 30000;
  const MAX_COMMAND_TIMEOUT_MS = 6 * 60 * 1000;
  const DEFAULT_ADAPTER_URL = '/static/game/sdk/neko-minigame-same-origin-host.js';

  function normalizeCommandRoutes(value) {
    if (value === undefined) return Object.freeze({});
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const entries = Object.entries(value);
    if (entries.length > COMMAND_ROUTE_LIMIT) return null;
    const routes = Object.create(null);
    for (const [name, rawPolicy] of entries) {
      if (!/^[a-z][a-z0-9:-]{0,63}$/.test(name)) return null;
      if (!rawPolicy || typeof rawPolicy !== 'object' || Array.isArray(rawPolicy)) return null;
      if (Object.keys(rawPolicy).some((key) => !['path', 'maxRequestBytes', 'maxTimeoutMs'].includes(key))) {
        return null;
      }
      const path = rawPolicy.path;
      if (
        typeof path !== 'string'
        || !/^[a-z][a-z0-9-]*(?:\/[a-z][a-z0-9-]*)*$/.test(path)
      ) return null;
      const maxRequestBytes = rawPolicy.maxRequestBytes === undefined
        ? DEFAULT_COMMAND_REQUEST_BYTES
        : rawPolicy.maxRequestBytes;
      const maxTimeoutMs = rawPolicy.maxTimeoutMs === undefined
        ? DEFAULT_COMMAND_TIMEOUT_MS
        : rawPolicy.maxTimeoutMs;
      if (
        !Number.isInteger(maxRequestBytes)
        || maxRequestBytes < 1
        || maxRequestBytes > MAX_COMMAND_REQUEST_BYTES
        || !Number.isInteger(maxTimeoutMs)
        || maxTimeoutMs < 250
        || maxTimeoutMs > MAX_COMMAND_TIMEOUT_MS
      ) return null;
      routes[name] = Object.freeze({ path, maxRequestBytes, maxTimeoutMs });
    }
    return Object.freeze(routes);
  }

  function normalizeRegistrations(value, providerRegistry = {}) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return Object.freeze({});
    const result = {};
    for (const [rawKey, rawRegistration] of Object.entries(value)) {
      if (Object.keys(result).length >= REGISTRATION_LIMIT) break;
      if (!rawRegistration || typeof rawRegistration !== 'object' || Array.isArray(rawRegistration)) continue;
      const gameId = String(rawRegistration.gameId || '').trim();
      const routeGameType = String(rawRegistration.routeGameType || gameId).trim();
      const version = String(rawRegistration.version || '').trim();
      const mode = String(rawRegistration.mode || '').trim();
      if (
        !gameId
        || gameId !== String(rawKey || '').trim()
        || gameId.length > 128
        || !/^[a-z][a-z0-9_-]{0,127}$/.test(routeGameType)
        || !version
        || version.length > 64
        || !['registered', 'development'].includes(mode)
      ) continue;
      const commandRoutes = normalizeCommandRoutes(rawRegistration.commandRoutes);
      if (!commandRoutes) continue;
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
        avatarHostFactory: typeof rawProviders?.avatarHostFactory === 'function'
          ? rawProviders.avatarHostFactory
          : null,
      });
      result[gameId] = Object.freeze({
        mode,
        gameId,
        routeGameType,
        publisherId: String(rawRegistration.publisherId || '').trim().slice(0, 128),
        version,
        allowedCapabilities,
        commandRoutes,
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
      if (Object.values(registrations).some(record => record.allowedCapabilities.includes('vision'))) {
        try {
          await loadAdapterScript('/static/game/sdk/neko-minigame-vision-host.js', documentImpl, Object.freeze({}));
        } catch (_) {
          // Vision is optional locally. The adapter negotiates availability;
          // a required vision capability still fails the normal handshake.
        }
      }
      await loadAdapterScript(adapterUrl, documentImpl, registrations);
      if (typeof window.createNekoMiniGameSameOriginHost !== 'function') {
        throw new Error('MINIGAME_HOST_ADAPTER_FACTORY_MISSING');
      }
      return window.createNekoMiniGameSameOriginHost;
    })();
})();
