(function () {
  'use strict';

  // 游戏音频配置规则：
  // - 音频配置归各游戏自己维护，不做一个全项目游戏音频总配置文件。
  // - 各游戏使用统一配置形状：{ bgm: {...}, sfx: {...}, useMoodBgm?: boolean }。
  // - 游戏音频系统只负责播放注册资源；什么时候播、为什么播，由具体游戏代码决定。
  // - 具体游戏可以把配置单独放在 static/game/games/<gameType>/ 下，再由页面加载。
  // - 如果希望 VS Code 在写 gameAudioConfig.bgm. 时给下拉提示，游戏代码应直接引用
  //   该配置对象变量；只通过 window.xxx 动态读取时，补全能力会弱一些。
  // - 对外只暴露 NekoGameSystem.GameAudioSystem；BGM 播放器和音效播放器是内部实现，
  //   不作为游戏代码直接调用的公共入口。

  const DEFAULT_BGM_VOLUME = 0.45;
  const DEFAULT_SFX_VOLUME = 0.75;
  const DEFAULT_FADE_MS = 800;
  const DEFAULT_BGM_STORAGE_KEY = 'neko.gameAudio.bgmVolume';
  const DEFAULT_SFX_STORAGE_KEY = 'neko.gameAudio.sfxVolume';
  const PLAYLIST_ID_PREFIX = 'playlist:';

  function clamp01(value, fallback) {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) return fallback;
    return Math.max(0, Math.min(1, numberValue));
  }

  function readStoredVolume(storageKey, fallback) {
    try {
      const stored = window.localStorage?.getItem(storageKey);
      if (stored !== null && stored !== undefined) return clamp01(stored, fallback);
    } catch (_err) {
      // 受限运行环境可能无法访问 localStorage。
    }
    return fallback;
  }

  function writeStoredVolume(storageKey, volume) {
    try {
      window.localStorage?.setItem(storageKey, String(volume));
    } catch (_err) {
      // 受限运行环境可能无法访问 localStorage。
    }
  }

  function getByPath(source, path) {
    if (!source || typeof path !== 'string' || !path.trim()) return undefined;
    return path.split('.').reduce((node, part) => {
      if (node === undefined || node === null) return undefined;
      return node[part];
    }, source);
  }

  function normalizeTrack(track) {
    if (typeof track === 'string') {
      const src = track.trim();
      return src ? { src } : null;
    }
    if (!track || typeof track !== 'object') return null;
    const src = String(track.src || track.url || '').trim();
    if (!src) return null;
    return { ...track, src };
  }

  function normalizeAudioList(value) {
    if (Array.isArray(value)) return value.map(normalizeTrack).filter(Boolean);
    const track = normalizeTrack(value);
    return track ? [track] : [];
  }

  function playlistSignature(playlist) {
    return playlist.map((track) => track.src).join('\n');
  }

  function disposeAudio(audio) {
    if (!audio) return;
    try {
      audio.pause();
      audio.currentTime = 0;
      audio.src = '';
      audio.load?.();
    } catch (_err) {
      // 释放音频资源失败不应影响游戏退出流程。
    }
  }

  class GameBgmPlayer {
    constructor(options = {}) {
      this.fadeMs = Math.max(0, Number(options.fadeMs ?? DEFAULT_FADE_MS) || 0);
      this.storageKey = String(options.storageKey || DEFAULT_BGM_STORAGE_KEY);
      this.audioFactory = typeof options.audioFactory === 'function'
        ? options.audioFactory
        : (src) => new Audio(src);
      this.random = typeof options.random === 'function' ? options.random : Math.random;
      this.persistVolume = options.persistVolume !== false;
      this.loopPlaylist = options.loopPlaylist !== false;
      this.onError = typeof options.onError === 'function' ? options.onError : null;

      this.volume = options.volume !== undefined
        ? clamp01(options.volume, DEFAULT_BGM_VOLUME)
        : (this.persistVolume ? readStoredVolume(this.storageKey, DEFAULT_BGM_VOLUME) : DEFAULT_BGM_VOLUME);
      this.currentAudio = null;
      this.fadingAudio = null;
      this.currentPlaylist = [];
      this.currentSignature = '';
      this.currentTrack = null;
      this.lastTrackBySignature = new Map();
      this.preloadCache = new Map();
      this.fadeTimer = null;
      this.pendingPlayAfterUnlock = false;
      this.destroyed = false;
      this.pausedByUser = false;
    }

    playPlaylist(playlist, options = {}) {
      if (this.destroyed) return Promise.resolve(false);

      const normalized = normalizeAudioList(playlist);
      const signature = options.id
        ? `${PLAYLIST_ID_PREFIX}${String(options.id)}`
        : playlistSignature(normalized);

      if (!normalized.length) {
        this.currentPlaylist = [];
        this.currentSignature = '';
        this.currentTrack = null;
        this.stop();
        return Promise.resolve(false);
      }

      const samePlaylist = signature && signature === this.currentSignature;
      if (samePlaylist && this.currentAudio && !options.force) {
        return Promise.resolve(true);
      }

      this.currentPlaylist = normalized;
      this.currentSignature = signature;
      this.pausedByUser = false;
      const nextTrack = this._pickTrack(normalized, signature);
      const result = this._crossfadeTo(nextTrack, options);
      this._preloadNextTrack();
      return result;
    }

    preload(playlist) {
      const tracks = normalizeAudioList(playlist);
      for (const track of tracks) this._preloadTrack(track);
    }

    unload(playlist) {
      const tracks = normalizeAudioList(playlist);
      for (const track of tracks) {
        const cached = this.preloadCache.get(track.src);
        if (cached) disposeAudio(cached);
        this.preloadCache.delete(track.src);
      }
    }

    setVolume(volume) {
      this.volume = clamp01(volume, this.volume);
      if (this.persistVolume) writeStoredVolume(this.storageKey, this.volume);
      this._applyVolume(this.currentAudio, this.volume);
      return this.volume;
    }

    pause() {
      this.pausedByUser = true;
      if (this.currentAudio) this.currentAudio.pause();
    }

    resume() {
      this.pausedByUser = false;
      if (!this.currentAudio) return Promise.resolve(false);
      return this._safePlay(this.currentAudio);
    }

    stop() {
      this.pendingPlayAfterUnlock = false;
      this._clearFadeTimer();
      disposeAudio(this.currentAudio);
      disposeAudio(this.fadingAudio);
      this.currentAudio = null;
      this.fadingAudio = null;
      this.currentTrack = null;
    }

    destroy() {
      this.stop();
      for (const audio of this.preloadCache.values()) disposeAudio(audio);
      this.preloadCache.clear();
      this.destroyed = true;
      this.currentPlaylist = [];
      this.currentSignature = '';
      this.lastTrackBySignature.clear();
    }

    unlock() {
      if (!this.pendingPlayAfterUnlock || !this.currentAudio || this.pausedByUser) {
        return Promise.resolve(false);
      }
      return this._safePlay(this.currentAudio);
    }

    _pickTrack(playlist, signature) {
      if (playlist.length === 1) return playlist[0];
      const lastTrack = this.lastTrackBySignature.get(signature);
      const candidates = lastTrack
        ? playlist.filter((track) => track.src !== lastTrack.src)
        : playlist;
      const pool = candidates.length ? candidates : playlist;
      const index = Math.floor(clamp01(this.random(), 0) * pool.length);
      return pool[Math.min(index, pool.length - 1)];
    }

    _crossfadeTo(track, options = {}) {
      if (!track || !track.src) return Promise.resolve(false);

      this._clearFadeTimer();
      const previousAudio = this.currentAudio;
      const nextAudio = this._createAudio(track);
      this.currentAudio = nextAudio;
      this.currentTrack = track;
      this.lastTrackBySignature.set(this.currentSignature, track);

      this._applyVolume(nextAudio, previousAudio ? 0 : this.volume);

      const playPromise = this._safePlay(nextAudio);
      const fadeMs = Math.max(0, Number(options.fadeMs ?? this.fadeMs) || 0);
      if (!previousAudio || fadeMs <= 0) {
        disposeAudio(previousAudio);
        this._applyVolume(nextAudio, this.volume);
        return playPromise;
      }

      this.fadingAudio = previousAudio;
      this._startCrossfade(previousAudio, nextAudio, fadeMs);
      return playPromise;
    }

    _createAudio(track) {
      const cached = this.preloadCache.get(track.src);
      if (cached) this.preloadCache.delete(track.src);
      const audio = cached || this.audioFactory(track.src, track);
      audio.preload = track.preload || 'auto';
      audio.loop = false;
      audio.addEventListener?.('ended', () => this._handleEnded(audio));
      audio.addEventListener?.('error', (event) => this._handleError(audio, event));
      return audio;
    }

    _preloadTrack(track) {
      if (!track || !track.src || this.preloadCache.has(track.src)) return;
      const audio = this.audioFactory(track.src, track);
      audio.preload = track.preload || 'auto';
      audio.volume = 0;
      try {
        audio.load?.();
      } catch (_err) {
        // 预加载是尽力行为，失败不影响后续播放尝试。
      }
      this.preloadCache.set(track.src, audio);
    }

    _preloadNextTrack() {
      if (!this.currentPlaylist.length) return;
      const candidates = this.currentTrack
        ? this.currentPlaylist.filter((track) => track.src !== this.currentTrack.src)
        : this.currentPlaylist;
      const pool = candidates.length ? candidates : this.currentPlaylist;
      const nextTrack = this._pickTrack(pool, `${this.currentSignature}:preload`);
      this._preloadTrack(nextTrack);
    }

    _startCrossfade(previousAudio, nextAudio, fadeMs) {
      const startedAt = Date.now();
      const previousStartVolume = Number(previousAudio.volume) || 0;

      this.fadeTimer = window.setInterval(() => {
        const elapsed = Date.now() - startedAt;
        const progress = Math.min(1, elapsed / fadeMs);
        this._applyVolume(previousAudio, previousStartVolume * (1 - progress));
        this._applyVolume(nextAudio, this.volume * progress);

        if (progress >= 1) {
          this._clearFadeTimer();
          disposeAudio(previousAudio);
          if (this.fadingAudio === previousAudio) this.fadingAudio = null;
          this._applyVolume(nextAudio, this.volume);
        }
      }, 50);
    }

    _handleEnded(audio) {
      if (audio !== this.currentAudio || this.pausedByUser || !this.loopPlaylist) return;
      const nextTrack = this._pickTrack(this.currentPlaylist, this.currentSignature);
      this._crossfadeTo(nextTrack);
      this._preloadNextTrack();
    }

    _handleError(audio, event) {
      if (typeof this.onError === 'function') {
        this.onError(event, {
          audio,
          track: this.currentTrack,
          playlist: this.currentPlaylist,
        });
      }
      if (audio !== this.currentAudio || this.pausedByUser) return;
      const remaining = this.currentPlaylist.filter((track) => track.src !== this.currentTrack?.src);
      if (!remaining.length) {
        this.stop();
        return;
      }
      const nextTrack = this._pickTrack(remaining, `${this.currentSignature}:error`);
      this._crossfadeTo(nextTrack, { fadeMs: 0 });
      this._preloadNextTrack();
    }

    _safePlay(audio) {
      if (!audio || this.pausedByUser || this.destroyed) return Promise.resolve(false);
      try {
        const result = audio.play();
        if (result && typeof result.then === 'function') {
          return result
            .then(() => {
              this.pendingPlayAfterUnlock = false;
              return true;
            })
            .catch(() => {
              this.pendingPlayAfterUnlock = true;
              return false;
            });
        }
        this.pendingPlayAfterUnlock = false;
        return Promise.resolve(true);
      } catch (_err) {
        this.pendingPlayAfterUnlock = true;
        return Promise.resolve(false);
      }
    }

    _applyVolume(audio, volume) {
      if (!audio) return;
      audio.volume = clamp01(volume, this.volume);
    }

    _clearFadeTimer() {
      if (!this.fadeTimer) return;
      window.clearInterval(this.fadeTimer);
      this.fadeTimer = null;
    }
  }

  class GameSfxPlayer {
    constructor(options = {}) {
      this.storageKey = String(options.storageKey || DEFAULT_SFX_STORAGE_KEY);
      this.audioFactory = typeof options.audioFactory === 'function'
        ? options.audioFactory
        : (src) => new Audio(src);
      this.random = typeof options.random === 'function' ? options.random : Math.random;
      this.persistVolume = options.persistVolume !== false;
      this.maxConcurrent = Math.max(1, Number(options.maxConcurrent || 12) || 12);
      this.onError = typeof options.onError === 'function' ? options.onError : null;
      this.volume = options.volume !== undefined
        ? clamp01(options.volume, DEFAULT_SFX_VOLUME)
        : (this.persistVolume ? readStoredVolume(this.storageKey, DEFAULT_SFX_VOLUME) : DEFAULT_SFX_VOLUME);
      this.baseCache = new Map();
      this.active = new Set();
      this.destroyed = false;
    }

    play(value, options = {}) {
      if (this.destroyed) return Promise.resolve(false);
      const tracks = normalizeAudioList(value);
      if (!tracks.length) return Promise.resolve(false);
      const track = this._pickTrack(tracks);
      return this._playTrack(track, options);
    }

    preload(value) {
      const tracks = normalizeAudioList(value);
      for (const track of tracks) this._getBaseAudio(track);
    }

    unload(value) {
      const tracks = normalizeAudioList(value);
      for (const track of tracks) {
        const cached = this.baseCache.get(track.src);
        if (cached) disposeAudio(cached);
        this.baseCache.delete(track.src);
      }
    }

    setVolume(volume) {
      this.volume = clamp01(volume, this.volume);
      if (this.persistVolume) writeStoredVolume(this.storageKey, this.volume);
      return this.volume;
    }

    destroy() {
      this.destroyed = true;
      for (const audio of this.baseCache.values()) disposeAudio(audio);
      for (const audio of this.active) disposeAudio(audio);
      this.baseCache.clear();
      this.active.clear();
    }

    _pickTrack(tracks) {
      if (tracks.length === 1) return tracks[0];
      const index = Math.floor(clamp01(this.random(), 0) * tracks.length);
      return tracks[Math.min(index, tracks.length - 1)];
    }

    _getBaseAudio(track) {
      if (!track || !track.src) return null;
      if (this.baseCache.has(track.src)) return this.baseCache.get(track.src);
      const audio = this.audioFactory(track.src, track);
      audio.preload = track.preload || 'auto';
      audio.volume = this.volume;
      try {
        audio.load?.();
      } catch (_err) {
        // 预加载是尽力行为，失败不影响后续播放尝试。
      }
      this.baseCache.set(track.src, audio);
      return audio;
    }

    _playTrack(track, options = {}) {
      if (this.active.size >= this.maxConcurrent) {
        const oldest = this.active.values().next().value;
        this._releaseInstance(oldest);
      }

      const base = this._getBaseAudio(track);
      if (!base) return Promise.resolve(false);
      const audio = typeof base.cloneNode === 'function'
        ? base.cloneNode(true)
        : this.audioFactory(track.src, track);
      audio.volume = clamp01(options.volume, this.volume);
      this.active.add(audio);

      const cleanup = () => this._releaseInstance(audio);
      audio.addEventListener?.('ended', cleanup, { once: true });
      audio.addEventListener?.('error', (event) => {
        if (typeof this.onError === 'function') this.onError(event, { audio, track });
        cleanup();
      }, { once: true });

      try {
        const result = audio.play();
        if (result && typeof result.then === 'function') {
          return result.then(() => true).catch((event) => {
            if (typeof this.onError === 'function') this.onError(event, { audio, track });
            cleanup();
            return false;
          });
        }
        return Promise.resolve(true);
      } catch (event) {
        if (typeof this.onError === 'function') this.onError(event, { audio, track });
        cleanup();
        return Promise.resolve(false);
      }
    }

    _releaseInstance(audio) {
      if (!audio || !this.active.has(audio)) return;
      this.active.delete(audio);
      disposeAudio(audio);
    }
  }

  class GameAudioSystem {
    constructor(options = {}) {
      this.config = { bgm: {}, sfx: {} };
      this.bgm = new GameBgmPlayer({
        ...options,
        volume: options.bgmVolume ?? options.volume,
        storageKey: options.bgmStorageKey || DEFAULT_BGM_STORAGE_KEY,
        onError: options.onBgmError || options.onError,
      });
      this.sfx = new GameSfxPlayer({
        ...options,
        volume: options.sfxVolume,
        storageKey: options.sfxStorageKey || DEFAULT_SFX_STORAGE_KEY,
        onError: options.onSfxError || options.onError,
      });
      if (options.config) this.configure(options.config);
    }

    configure(config = {}) {
      this.config = {
        bgm: config.bgm || {},
        sfx: config.sfx || {},
      };
      return this.config;
    }

    playBgm(keyOrPlaylist, options = {}) {
      const playlist = this._resolveBgm(keyOrPlaylist);
      return this.bgm.playPlaylist(playlist, {
        id: typeof keyOrPlaylist === 'string' ? keyOrPlaylist : options.id,
        ...options,
      });
    }

    playSfx(keyOrAudio, options = {}) {
      return this.sfx.play(this._resolveSfx(keyOrAudio), options);
    }

    preloadBgm(keyOrPlaylist) {
      this.bgm.preload(this._resolveBgm(keyOrPlaylist));
    }

    preloadSfx(keyOrAudio) {
      this.sfx.preload(this._resolveSfx(keyOrAudio));
    }

    unloadBgm(keyOrPlaylist) {
      this.bgm.unload(this._resolveBgm(keyOrPlaylist));
    }

    unloadSfx(keyOrAudio) {
      this.sfx.unload(this._resolveSfx(keyOrAudio));
    }

    setBgmVolume(volume) {
      return this.bgm.setVolume(volume);
    }

    getBgmVolume() {
      return this.bgm.volume;
    }

    setSfxVolume(volume) {
      return this.sfx.setVolume(volume);
    }

    getSfxVolume() {
      return this.sfx.volume;
    }

    stopBgm() {
      this.bgm.stop();
    }

    pauseBgm() {
      this.bgm.pause();
    }

    resumeBgm() {
      return this.bgm.resume();
    }

    unlock() {
      return this.bgm.unlock();
    }

    destroy() {
      this.bgm.destroy();
      this.sfx.destroy();
    }

    _resolveBgm(value) {
      return typeof value === 'string' ? getByPath(this.config.bgm, value) : value;
    }

    _resolveSfx(value) {
      return typeof value === 'string' ? getByPath(this.config.sfx, value) : value;
    }
  }

  const gameSystem = window.NekoGameSystem || (window.NekoGameSystem = {});
  gameSystem.GameAudioSystem = GameAudioSystem;
})();
