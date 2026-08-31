/**
 * N.E.K.O Mini-Game Audio trusted host.
 *
 * Mini-games may remain silent. A game that emits BGM or SFX must mount the
 * official audio capability through NekoMiniGame instead of constructing this
 * host, GameAudioSystem, Audio, or WebAudio playback directly.
 */
(function (global) {
  'use strict';

  const DEFAULT_MAX_CONTROLLERS = 4;
  const MAX_ERROR_LISTENERS = 16;

  class MiniGameAudioHostError extends Error {
    constructor(code, message, details = {}) {
      super(message);
      this.name = 'MiniGameAudioHostError';
      this.code = String(code || 'request_failed');
      this.details = details && typeof details === 'object' ? details : {};
    }
  }

  function boundedPositiveInteger(value, fallback, maximum) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) return fallback;
    return Math.max(1, Math.min(Math.floor(numeric), maximum));
  }

  function safeIdentifier(value, fallback) {
    const normalized = String(value || '').trim().toLowerCase().replace(/[^a-z0-9-]+/g, '-');
    return normalized.replace(/^-+|-+$/g, '').slice(0, 64) || fallback;
  }

  function normalizePlaybackError(channel, event, context = {}) {
    const audio = context.audio || null;
    const mediaError = audio?.error || null;
    return Object.freeze({
      channel: String(channel || 'audio'),
      src: String(context.track?.src || audio?.currentSrc || audio?.src || '').slice(0, 2048),
      code: String(mediaError?.code || event?.code || ''),
      message: String(mediaError?.message || event?.message || event?.type || event || 'audio_failed')
        .slice(0, 500),
      networkState: Number.isFinite(Number(audio?.networkState)) ? Number(audio.networkState) : null,
      readyState: Number.isFinite(Number(audio?.readyState)) ? Number(audio.readyState) : null,
    });
  }

  function create(options = {}) {
    const AudioSystem = options.AudioSystem || global.NekoGameSystem?.GameAudioSystem;
    if (typeof AudioSystem !== 'function') {
      throw new MiniGameAudioHostError(
        'transport_unavailable',
        'N.E.K.O GameAudioSystem is unavailable',
      );
    }

    const controllers = new Set();
    const maxControllers = boundedPositiveInteger(
      options.maxControllers,
      DEFAULT_MAX_CONTROLLERS,
      16,
    );
    const storagePrefix = String(options.storagePrefix || 'neko.minigameAudio').slice(0, 128);
    const storageKeys = options.storageKeys && typeof options.storageKeys === 'object'
      ? options.storageKeys
      : {};
    let disposed = false;

    function mount(config = {}) {
      if (disposed) {
        throw new MiniGameAudioHostError('disposed', 'The mini-game audio host has been disposed');
      }
      if (controllers.size >= maxControllers) {
        throw new MiniGameAudioHostError('busy', 'The mini-game audio controller limit was reached', {
          limit: maxControllers,
        });
      }

      const slot = safeIdentifier(config.slot, 'main');
      const gameId = safeIdentifier(config.gameId, 'game');
      const settings = config.settings && typeof config.settings === 'object' ? config.settings : {};
      const errorListeners = new Set();
      const reportError = (channel) => (event, context) => {
        const payload = normalizePlaybackError(channel, event, context);
        for (const listener of Array.from(errorListeners)) {
          try { listener(payload); }
          catch (error) { global.console?.error?.('[NekoMiniGameAudioHost] error listener failed', error); }
        }
      };
      const system = new AudioSystem({
        config: config.resources || {},
        fadeMs: settings.fadeMs,
        bgmVolume: settings.bgmVolume,
        sfxVolume: settings.sfxVolume,
        persistVolume: settings.persistVolume !== false,
        maxConcurrent: settings.maxConcurrent,
        maxPreloadEntries: settings.maxPreloadEntries,
        maxPlaylistHistory: settings.maxPlaylistHistory,
        maxEndWaiters: settings.maxEndWaiters,
        bgmStorageKey: String(
          storageKeys.bgm || `${storagePrefix}.${gameId}.${slot}.bgmVolume`,
        ).slice(0, 256),
        sfxStorageKey: String(
          storageKeys.sfx || `${storagePrefix}.${gameId}.${slot}.sfxVolume`,
        ).slice(0, 256),
        ...(typeof options.audioFactory === 'function' ? { audioFactory: options.audioFactory } : {}),
        onBgmError: reportError('bgm'),
        onLoopedBgmError: reportError('bgm'),
        onSfxError: reportError('sfx'),
      });
      const state = { disposed: false, system: null };

      function requireActive(operation) {
        if (disposed || state.disposed || !state.system) {
          throw new MiniGameAudioHostError('disposed', 'The mini-game audio controller has been disposed', {
            operation,
          });
        }
        return state.system;
      }

      function disposeController() {
        if (state.disposed) return;
        state.disposed = true;
        controllers.delete(controller);
        errorListeners.clear();
        const activeSystem = state.system;
        state.system = null;
        try { activeSystem?.destroy?.(); }
        catch (error) { global.console?.error?.('[NekoMiniGameAudioHost] destroy failed', error); }
      }

      const controller = Object.freeze({
        slot,
        get disposed() { return disposed || state.disposed; },
        configure(resources = {}) {
          return requireActive('audio.configure').configure(resources);
        },
        playBgm(value, playOptions = {}) {
          return requireActive('audio.playBgm').playBgm(value, playOptions);
        },
        waitForBgmEnd(waitOptions = {}) {
          return requireActive('audio.waitForBgmEnd').waitForBgmEnd(waitOptions);
        },
        playLoopedBgm(value, playOptions = {}) {
          return requireActive('audio.playLoopedBgm').playLoopedBgm(value, playOptions);
        },
        stopLoopedBgm(stopOptions = {}) {
          return requireActive('audio.stopLoopedBgm').stopLoopedBgm(stopOptions);
        },
        finishLoopedBgm() {
          return requireActive('audio.finishLoopedBgm').finishLoopedBgm();
        },
        playSfx(value, playOptions = {}) {
          return requireActive('audio.playSfx').playSfx(value, playOptions);
        },
        preloadBgm(value) { return requireActive('audio.preloadBgm').preloadBgm(value); },
        preloadLoopedBgm(value) {
          return requireActive('audio.preloadLoopedBgm').preloadLoopedBgm(value);
        },
        preloadSfx(value) { return requireActive('audio.preloadSfx').preloadSfx(value); },
        unloadBgm(value) { return requireActive('audio.unloadBgm').unloadBgm(value); },
        unloadLoopedBgm(value) {
          return requireActive('audio.unloadLoopedBgm').unloadLoopedBgm(value);
        },
        unloadSfx(value) { return requireActive('audio.unloadSfx').unloadSfx(value); },
        setBgmVolume(value) { return requireActive('audio.setBgmVolume').setBgmVolume(value); },
        getBgmVolume() { return requireActive('audio.getBgmVolume').getBgmVolume(); },
        setSfxVolume(value) { return requireActive('audio.setSfxVolume').setSfxVolume(value); },
        getSfxVolume() { return requireActive('audio.getSfxVolume').getSfxVolume(); },
        getCurrentBgmSrc() {
          return requireActive('audio.getCurrentBgmSrc').getCurrentBgmSrc();
        },
        isCurrentBgm(value) { return requireActive('audio.isCurrentBgm').isCurrentBgm(value); },
        pauseBgm() { return requireActive('audio.pauseBgm').pauseBgm(); },
        resumeBgm() { return requireActive('audio.resumeBgm').resumeBgm(); },
        stopBgm() { return requireActive('audio.stopBgm').stopBgm(); },
        unlock() { return requireActive('audio.unlock').unlock(); },
        onError(listener) {
          requireActive('audio.onError');
          if (typeof listener !== 'function') {
            throw new MiniGameAudioHostError('invalid_request', 'Audio error listener must be a function');
          }
          if (errorListeners.size >= MAX_ERROR_LISTENERS) {
            throw new MiniGameAudioHostError('busy', 'Audio error listener limit was reached', {
              limit: MAX_ERROR_LISTENERS,
            });
          }
          errorListeners.add(listener);
          let active = true;
          return () => {
            if (!active) return;
            active = false;
            errorListeners.delete(listener);
          };
        },
        getState() {
          const activeSystem = requireActive('audio.getState');
          return Object.freeze({
            slot,
            bgmVolume: Number(activeSystem.getBgmVolume()),
            sfxVolume: Number(activeSystem.getSfxVolume()),
            currentBgmSrc: String(activeSystem.getCurrentBgmSrc() || ''),
          });
        },
        dispose: disposeController,
      });
      state.system = system;
      controllers.add(controller);
      return controller;
    }

    return Object.freeze({
      get disposed() { return disposed; },
      get activeCount() { return controllers.size; },
      mount,
      dispose() {
        if (disposed) return;
        disposed = true;
        for (const controller of Array.from(controllers)) controller.dispose();
        controllers.clear();
      },
    });
  }

  global.NekoMiniGameAudioHost = Object.freeze({
    create,
    Error: MiniGameAudioHostError,
  });
})(window);
