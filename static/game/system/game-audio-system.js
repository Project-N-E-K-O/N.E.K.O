(function () {
  'use strict';

  // 游戏音频配置规则：
  // - 音频配置归各游戏自己维护，不做一个全项目游戏音频总配置文件。
  // - 各游戏使用统一配置形状：{ bgm: {...}, loopedBgm: {...}, sfx: {...} }。
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

  function normalizeLoopedBgmConfig(value) {
    if (!value || typeof value !== 'object') return null;
    const intro = normalizeTrack(value.intro);
    const loop = normalizeTrack(value.loop);
    const outro = normalizeTrack(value.outro);
    if (!loop) return null;
    return { intro, loop, outro };
  }

  function playlistSignature(playlist) {
    return playlist.map((track) => track.src).join('\n');
  }

  /**
   * 生成普通 BGM 的身份标识。
   *
   * @param {string|string[]|Object|Object[]} value 注册 key 对应的歌单，或直接传入的歌单。
   * @param {string} [id] 调用方显式指定的身份；传 key 播放时会使用 key。
   * @returns {string} 用于判定“当前是否同一套普通 BGM”的内部标识。
   */
  function bgmIdentityFromPlaylist(value, id = '') {
    if (id) return `${PLAYLIST_ID_PREFIX}${String(id)}`;
    return playlistSignature(normalizeAudioList(value));
  }

  /**
   * 生成循环 BGM 的身份标识。
   *
   * @param {Object} value 循环 BGM 配置，形如 { intro?, loop, outro? }。
   * @param {string} [id] 调用方显式指定的身份；传 key 播放时会使用 key。
   * @returns {string} 用于判定“当前是否同一套循环 BGM”的内部标识。
   */
  function loopedBgmIdentityFromConfig(value, id = '') {
    if (id) return String(id);
    const config = normalizeLoopedBgmConfig(value);
    if (!config) return '';
    return [config.intro?.src || '', config.loop?.src || '', config.outro?.src || ''].join('\n');
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
      this.currentContentSignature = '';
      this.currentTrack = null;
      this.lastTrackBySignature = new Map();
      this.preloadCache = new Map();
      this.fadeTimer = null;
      this.pendingPlayAfterUnlock = false;
      this.destroyed = false;
      this.pausedByUser = false;
    }

    /**
     * 播放普通 BGM 歌单。
     *
     * @param {string|string[]|Object|Object[]} playlist 直接传入的歌单或单个音频。
     * @param {Object} [options] 播放选项。
     * @param {string} [options.id] 本次播放身份；用于判重，避免同一套 BGM 重复启动。
     * @param {boolean} [options.force] 是否强制重播同一套 BGM。
     * @param {number} [options.fadeMs] 本次切换淡入淡出时间。
     * @returns {Promise<boolean>} 是否成功发起播放。
     */
    playPlaylist(playlist, options = {}) {
      if (this.destroyed) return Promise.resolve(false);

      const normalized = normalizeAudioList(playlist);
      const signature = bgmIdentityFromPlaylist(normalized, options.id);

      if (!normalized.length) {
        this.currentPlaylist = [];
        this.currentSignature = '';
        this.currentContentSignature = '';
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
      this.currentContentSignature = playlistSignature(normalized);
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
      this.currentContentSignature = '';
      this.lastTrackBySignature.clear();
    }

    unlock() {
      if (!this.pendingPlayAfterUnlock || !this.currentAudio || this.pausedByUser) {
        return Promise.resolve(false);
      }
      return this._safePlay(this.currentAudio);
    }

    getCurrentSrc() {
      return this.currentTrack?.src || '';
    }

    /**
     * 判断当前普通 BGM 是否等于传入内容。
     *
     * @param {string|string[]|Object|Object[]} value 歌单或单个音频。
     * @param {string} [id] 与 playPlaylist(options.id) 相同的身份。
     * @returns {boolean} 当前普通 BGM 是否与传入内容相同。
     */
    isCurrent(value, id = '') {
      if (!this.currentAudio) return false;
      const identity = bgmIdentityFromPlaylist(value, id);
      const contentIdentity = bgmIdentityFromPlaylist(value);
      return identity === this.currentSignature || contentIdentity === this.currentContentSignature;
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

  class GameLoopedBgmPlayer {
    constructor(options = {}) {
      this.fadeMs = Math.max(0, Number(options.fadeMs ?? DEFAULT_FADE_MS) || 0);
      this.audioFactory = typeof options.audioFactory === 'function'
        ? options.audioFactory
        : (src) => new Audio(src);
      this.onError = typeof options.onError === 'function' ? options.onError : null;
      this.volume = options.volume !== undefined
        ? clamp01(options.volume, DEFAULT_BGM_VOLUME)
        : DEFAULT_BGM_VOLUME;
      this.currentAudio = null;
      this.currentConfig = null;
      this.currentId = '';
      this.currentContentSignature = '';
      this.currentTrack = null;
      this.phase = 'stopped';
      this.pendingFinish = false;
      this.pendingPlayAfterUnlock = false;
      this.pausedByUser = false;
      this.destroyed = false;
      this.fadeTimer = null;
      this.preloadCache = new Map();
    }

    // 播放循环 BGM：
    // - intro：可选，只播放一次。
    // - loop：必填，循环段；没有收尾请求时反复播放。
    // - outro：可选，finishLoopedBgm() 请求收尾后播放。
    /**
     * 播放循环 BGM。
     *
     * @param {Object} config 循环 BGM 配置，形如 { intro?, loop, outro? }。
     * @param {Object} [options] 播放选项。
     * @param {string} [options.id] 本次播放身份；传 key 播放时会使用 key。
     * @param {boolean} [options.force] 是否强制从 intro 或 loop 重新开始。
     * @returns {Promise<boolean>} 是否成功发起播放。
     */
    play(config, options = {}) {
      if (this.destroyed) return Promise.resolve(false);
      const normalized = normalizeLoopedBgmConfig(config);
      if (!normalized) {
        this.stop();
        return Promise.resolve(false);
      }

      const id = loopedBgmIdentityFromConfig(normalized, options.id);
      if (id === this.currentId && this.currentAudio && !options.force) {
        return Promise.resolve(true);
      }

      this.stop({ fadeMs: 0 });
      this.currentConfig = normalized;
      this.currentId = id;
      this.currentContentSignature = loopedBgmIdentityFromConfig(normalized);
      this.pendingFinish = false;
      this.pausedByUser = false;
      return this._playPhase(normalized.intro ? 'intro' : 'loop');
    }

    /**
     * 预加载循环 BGM 的 intro / loop / outro。
     *
     * @param {Object} config 循环 BGM 配置。
     */
    preload(config) {
      const normalized = normalizeLoopedBgmConfig(config);
      if (!normalized) return;
      [normalized.intro, normalized.loop, normalized.outro].forEach((track) => this._preloadTrack(track));
    }

    /**
     * 卸载循环 BGM 的预加载缓存。
     *
     * @param {Object} config 循环 BGM 配置。
     */
    unload(config) {
      const normalized = normalizeLoopedBgmConfig(config);
      if (!normalized) return;
      [normalized.intro, normalized.loop, normalized.outro].forEach((track) => {
        if (!track?.src) return;
        const cached = this.preloadCache.get(track.src);
        if (cached) disposeAudio(cached);
        this.preloadCache.delete(track.src);
      });
    }

    setVolume(volume) {
      this.volume = clamp01(volume, this.volume);
      this._applyVolume(this.currentAudio, this.volume);
      return this.volume;
    }

    // 立即停止循环 BGM。用于强制退出页面、强制切换到普通 BGM 等场景。
    stop(options = {}) {
      this.pendingFinish = false;
      this.pendingPlayAfterUnlock = false;
      this._clearFadeTimer();
      const audio = this.currentAudio;
      this.currentAudio = null;
      this.currentTrack = null;
      this.currentConfig = null;
      this.currentId = '';
      this.currentContentSignature = '';
      this.phase = 'stopped';
      const fadeMs = Math.max(0, Number(options.fadeMs ?? 0) || 0);
      if (audio && fadeMs > 0) this._fadeOutAndDispose(audio, fadeMs);
      else disposeAudio(audio);
    }

    // 收尾循环 BGM。不会立刻打断当前段：
    // - 当前在 intro：intro 结束后播放 outro；没有 outro 就结束。
    // - 当前在 loop：当前 loop 结束后播放 outro；没有 outro 就结束。
    // - 当前在 outro：保持 outro 播完。
    finish() {
      if (!this.currentAudio || !this.currentConfig || this.phase === 'stopped') {
        return Promise.resolve(false);
      }
      if (this.phase === 'outro') return Promise.resolve(true);
      this.pendingFinish = true;
      return Promise.resolve(true);
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

    unlock() {
      if (!this.pendingPlayAfterUnlock || !this.currentAudio || this.pausedByUser) {
        return Promise.resolve(false);
      }
      return this._safePlay(this.currentAudio);
    }

    destroy() {
      this.stop({ fadeMs: 0 });
      for (const audio of this.preloadCache.values()) disposeAudio(audio);
      this.preloadCache.clear();
      this.destroyed = true;
    }

    getCurrentSrc() {
      return this.currentTrack?.src || '';
    }

    /**
     * 判断当前循环 BGM 是否等于传入内容。
     *
     * @param {Object} value 循环 BGM 配置。
     * @param {string} [id] 与 play(options.id) 相同的身份。
     * @returns {boolean} 当前循环 BGM 是否与传入内容相同；intro / loop / outro 任意阶段都算同一套。
     */
    isCurrent(value, id = '') {
      if (!this.currentAudio) return false;
      const identity = loopedBgmIdentityFromConfig(value, id);
      const contentIdentity = loopedBgmIdentityFromConfig(value);
      return identity === this.currentId || contentIdentity === this.currentContentSignature;
    }

    _playPhase(phase) {
      if (!this.currentConfig || this.destroyed) return Promise.resolve(false);
      const track = this.currentConfig[phase];
      if (!track) {
        if (phase === 'outro') {
          this.stop({ fadeMs: 0 });
          return Promise.resolve(false);
        }
        return this._playPhase('loop');
      }

      this._clearFadeTimer();
      const previousAudio = this.currentAudio;
      const audio = this._createAudio(track, phase);
      this.currentAudio = audio;
      this.currentTrack = track;
      this.phase = phase;
      this._applyVolume(audio, previousAudio ? 0 : this.volume);
      const playPromise = this._safePlay(audio);

      const fadeMs = previousAudio ? this.fadeMs : 0;
      if (previousAudio && fadeMs > 0) {
        this._startCrossfade(previousAudio, audio, fadeMs);
      } else {
        disposeAudio(previousAudio);
        this._applyVolume(audio, this.volume);
      }
      return playPromise;
    }

    _handleEnded(audio, phase) {
      if (audio !== this.currentAudio || this.pausedByUser || this.destroyed) return;
      if (phase === 'outro') {
        this.stop({ fadeMs: 0 });
        return;
      }
      if (this.pendingFinish) {
        this.pendingFinish = false;
        if (this.currentConfig?.outro) {
          this._playPhase('outro');
        } else {
          this.stop({ fadeMs: 0 });
        }
        return;
      }
      this._playPhase('loop');
    }

    _handleError(audio, event, phase) {
      if (typeof this.onError === 'function') {
        this.onError(event, {
          audio,
          track: this.currentTrack,
          phase,
          config: this.currentConfig,
        });
      }
      if (audio !== this.currentAudio || this.pausedByUser || this.destroyed) return;
      if (phase === 'intro') {
        this._playPhase(this.pendingFinish && this.currentConfig?.outro ? 'outro' : 'loop');
        return;
      }
      if (phase === 'loop' && this.currentConfig?.outro && this.pendingFinish) {
        this._playPhase('outro');
        return;
      }
      this.stop({ fadeMs: 0 });
    }

    _createAudio(track, phase) {
      const cached = this.preloadCache.get(track.src);
      if (cached) this.preloadCache.delete(track.src);
      const audio = cached || this.audioFactory(track.src, track);
      audio.preload = track.preload || 'auto';
      audio.loop = false;
      audio.addEventListener?.('ended', () => this._handleEnded(audio, phase));
      audio.addEventListener?.('error', (event) => this._handleError(audio, event, phase));
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
          this._applyVolume(nextAudio, this.volume);
        }
      }, 50);
    }

    _fadeOutAndDispose(audio, fadeMs) {
      const startedAt = Date.now();
      const startVolume = Number(audio.volume) || 0;
      this.fadeTimer = window.setInterval(() => {
        const elapsed = Date.now() - startedAt;
        const progress = Math.min(1, elapsed / fadeMs);
        this._applyVolume(audio, startVolume * (1 - progress));
        if (progress >= 1) {
          this._clearFadeTimer();
          disposeAudio(audio);
        }
      }, 50);
    }

    _clearFadeTimer() {
      if (!this.fadeTimer) return;
      window.clearInterval(this.fadeTimer);
      this.fadeTimer = null;
    }
  }

  class GameAudioSystem {
    constructor(options = {}) {
      this.config = { bgm: {}, loopedBgm: {}, sfx: {} };
      this.bgm = new GameBgmPlayer({
        ...options,
        volume: options.bgmVolume ?? options.volume,
        storageKey: options.bgmStorageKey || DEFAULT_BGM_STORAGE_KEY,
        onError: options.onBgmError || options.onError,
      });
      this.loopedBgm = new GameLoopedBgmPlayer({
        ...options,
        volume: this.bgm.volume,
        onError: options.onLoopedBgmError || options.onBgmError || options.onError,
      });
      this.sfx = new GameSfxPlayer({
        ...options,
        volume: options.sfxVolume,
        storageKey: options.sfxStorageKey || DEFAULT_SFX_STORAGE_KEY,
        onError: options.onSfxError || options.onError,
      });
      if (options.config) this.configure(options.config);
    }

    /**
     * 注册游戏音频资源。
     *
     * @param {Object} config 配置对象。
     * @param {Object} [config.bgm] 普通 BGM 资源树。
     * @param {Object} [config.loopedBgm] 循环 BGM 资源树，叶子为 { intro?, loop, outro? }。
     * @param {Object} [config.sfx] 短音效资源树。
     * @returns {Object} 归一后的当前配置引用。
     */
    configure(config = {}) {
      this.config = {
        bgm: config.bgm || {},
        loopedBgm: config.loopedBgm || {},
        sfx: config.sfx || {},
      };
      return this.config;
    }

    /**
     * 播放普通 BGM。
     *
     * @param {string|string[]|Object|Object[]} keyOrPlaylist 注册 key，或直接传入歌单 / 单个音频。
     * @param {Object} [options] 播放选项。
     * @param {string} [options.id] 直接传歌单时可指定身份；传 key 时默认用 key。
     * @param {boolean} [options.force] 是否强制重播同一套 BGM。
     * @param {number} [options.fadeMs] 本次切换淡入淡出时间。
     * @returns {Promise<boolean>} 是否成功发起播放。
     */
    playBgm(keyOrPlaylist, options = {}) {
      const playlist = this._resolveBgm(keyOrPlaylist);
      this.loopedBgm.stop({ fadeMs: options.fadeMs ?? 0 });
      return this.bgm.playPlaylist(playlist, {
        id: typeof keyOrPlaylist === 'string' ? keyOrPlaylist : options.id,
        ...options,
      });
    }

    /**
     * 播放循环 BGM。
     *
     * @param {string|Object} keyOrConfig 注册 key，或直接传入 { intro?, loop, outro? }。
     * @param {Object} [options] 播放选项。
     * @param {string} [options.id] 直接传配置时可指定身份；传 key 时默认用 key。
     * @param {boolean} [options.force] 是否强制从 intro 或 loop 重新开始。
     * @returns {Promise<boolean>} 是否成功发起播放。
     */
    playLoopedBgm(keyOrConfig, options = {}) {
      const config = this._resolveLoopedBgm(keyOrConfig);
      this.bgm.stop();
      return this.loopedBgm.play(config, {
        id: typeof keyOrConfig === 'string' ? keyOrConfig : options.id,
        ...options,
      });
    }

    /**
     * 立即停止循环 BGM。
     *
     * @param {Object} [options] 停止选项。
     * @param {number} [options.fadeMs] 淡出时间；不传时立即停止。
     */
    stopLoopedBgm(options = {}) {
      this.loopedBgm.stop(options);
    }

    /**
     * 收尾循环 BGM。
     *
     * 当前在 intro / loop 时不会立刻切断；会等待当前段结束后播放 outro，没有 outro 则停止。
     *
     * @returns {Promise<boolean>} 是否存在可收尾的循环 BGM。
     */
    finishLoopedBgm() {
      return this.loopedBgm.finish();
    }

    /**
     * 播放短音效。
     *
     * @param {string|string[]|Object|Object[]} keyOrAudio 注册 key，或直接传入音效资源。
     * @param {Object} [options] 播放选项。
     * @param {number} [options.volume] 本次播放临时音量，默认使用 SFX 音量。
     * @returns {Promise<boolean>} 是否成功发起播放。
     */
    playSfx(keyOrAudio, options = {}) {
      return this.sfx.play(this._resolveSfx(keyOrAudio), options);
    }

    /**
     * 预加载普通 BGM。
     *
     * @param {string|string[]|Object|Object[]} keyOrPlaylist 注册 key，或直接传入歌单。
     */
    preloadBgm(keyOrPlaylist) {
      this.bgm.preload(this._resolveBgm(keyOrPlaylist));
    }

    /**
     * 预加载循环 BGM 的 intro / loop / outro。
     *
     * @param {string|Object} keyOrConfig 注册 key，或直接传入循环 BGM 配置。
     */
    preloadLoopedBgm(keyOrConfig) {
      this.loopedBgm.preload(this._resolveLoopedBgm(keyOrConfig));
    }

    /**
     * 预加载短音效。
     *
     * @param {string|string[]|Object|Object[]} keyOrAudio 注册 key，或直接传入音效资源。
     */
    preloadSfx(keyOrAudio) {
      this.sfx.preload(this._resolveSfx(keyOrAudio));
    }

    /**
     * 卸载普通 BGM 预加载缓存。
     *
     * @param {string|string[]|Object|Object[]} keyOrPlaylist 注册 key，或直接传入歌单。
     */
    unloadBgm(keyOrPlaylist) {
      this.bgm.unload(this._resolveBgm(keyOrPlaylist));
    }

    /**
     * 卸载循环 BGM 预加载缓存。
     *
     * @param {string|Object} keyOrConfig 注册 key，或直接传入循环 BGM 配置。
     */
    unloadLoopedBgm(keyOrConfig) {
      this.loopedBgm.unload(this._resolveLoopedBgm(keyOrConfig));
    }

    /**
     * 卸载短音效预加载缓存。
     *
     * @param {string|string[]|Object|Object[]} keyOrAudio 注册 key，或直接传入音效资源。
     */
    unloadSfx(keyOrAudio) {
      this.sfx.unload(this._resolveSfx(keyOrAudio));
    }

    /**
     * 设置 BGM 音量。
     *
     * 普通 BGM 与循环 BGM 共用该音量。
     *
     * @param {number} volume 0 到 1 之间的音量值。
     * @returns {number} 实际保存的音量值。
     */
    setBgmVolume(volume) {
      const nextVolume = this.bgm.setVolume(volume);
      this.loopedBgm.setVolume(nextVolume);
      return nextVolume;
    }

    /**
     * 获取 BGM 音量。
     *
     * @returns {number} 当前 BGM 音量，范围 0 到 1。
     */
    getBgmVolume() {
      return this.bgm.volume;
    }

    /**
     * 获取当前正在播放的 BGM 文件路径。
     *
     * 该函数用于调试和展示；如果要判断是否同一套 BGM，优先使用 isCurrentBgm。
     *
     * @returns {string} 当前音频文件路径；没有 BGM 时返回空字符串。
     */
    getCurrentBgmSrc() {
      return this.loopedBgm.getCurrentSrc() || this.bgm.getCurrentSrc();
    }

    /**
     * 判断当前 BGM 是否等于传入内容。
     *
     * 调用方可以传入和 playBgm / playLoopedBgm 相同的内容。
     * 循环 BGM 在 intro / loop / outro 任意阶段都算同一套 BGM。
     *
     * @param {string|string[]|Object|Object[]} keyOrConfig 普通 BGM key、歌单、循环 BGM key 或循环 BGM 配置。
     * @returns {boolean} 当前 BGM 是否与传入内容相同。
     */
    isCurrentBgm(keyOrConfig) {
      if (typeof keyOrConfig === 'string') {
        return this.bgm.isCurrent(this._resolveBgm(keyOrConfig), keyOrConfig) ||
          this.loopedBgm.isCurrent(this._resolveLoopedBgm(keyOrConfig), keyOrConfig);
      }
      return this.bgm.isCurrent(keyOrConfig) || this.loopedBgm.isCurrent(keyOrConfig);
    }

    /**
     * 设置短音效音量。
     *
     * @param {number} volume 0 到 1 之间的音量值。
     * @returns {number} 实际保存的音量值。
     */
    setSfxVolume(volume) {
      return this.sfx.setVolume(volume);
    }

    /**
     * 获取短音效音量。
     *
     * @returns {number} 当前 SFX 音量，范围 0 到 1。
     */
    getSfxVolume() {
      return this.sfx.volume;
    }

    /**
     * 停止所有 BGM。
     *
     * 会同时停止普通 BGM 与循环 BGM。
     */
    stopBgm() {
      this.bgm.stop();
      this.loopedBgm.stop();
    }

    /**
     * 暂停所有 BGM。
     *
     * 只影响普通 BGM 与循环 BGM，不影响已经触发的短音效。
     */
    pauseBgm() {
      this.bgm.pause();
      this.loopedBgm.pause();
    }

    /**
     * 恢复所有已暂停的 BGM。
     *
     * @returns {Promise<boolean>} 是否至少有一个 BGM 播放器成功恢复。
     */
    resumeBgm() {
      return Promise.all([this.bgm.resume(), this.loopedBgm.resume()])
        .then((results) => results.some(Boolean));
    }

    /**
     * 解锁浏览器音频播放权限。
     *
     * 浏览器通常要求用户交互后才能播放带声音的音频；游戏入口可在点击开始时调用。
     *
     * @returns {Promise<boolean>} 是否至少有一个 BGM 播放器完成解锁。
     */
    unlock() {
      return Promise.all([this.bgm.unlock(), this.loopedBgm.unlock()])
        .then((results) => results.some(Boolean));
    }

    /**
     * 销毁音频系统。
     *
     * 会停止 BGM，清理循环 BGM 状态，并释放短音效缓存。
     */
    destroy() {
      this.bgm.destroy();
      this.loopedBgm.destroy();
      this.sfx.destroy();
    }

    /**
     * 解析普通 BGM 注册 key。
     *
     * @param {string|string[]|Object|Object[]} value 普通 BGM key 或直接传入的歌单。
     * @returns {string|string[]|Object|Object[]|undefined} 解析后的普通 BGM 配置。
     */
    _resolveBgm(value) {
      return typeof value === 'string' ? getByPath(this.config.bgm, value) : value;
    }

    /**
     * 解析循环 BGM 注册 key。
     *
     * @param {string|Object} value 循环 BGM key 或直接传入的循环 BGM 配置。
     * @returns {Object|undefined} 解析后的循环 BGM 配置。
     */
    _resolveLoopedBgm(value) {
      return typeof value === 'string' ? getByPath(this.config.loopedBgm, value) : value;
    }

    /**
     * 解析短音效注册 key。
     *
     * @param {string|string[]|Object|Object[]} value 短音效 key 或直接传入的音效配置。
     * @returns {string|string[]|Object|Object[]|undefined} 解析后的短音效配置。
     */
    _resolveSfx(value) {
      return typeof value === 'string' ? getByPath(this.config.sfx, value) : value;
    }
  }

  const gameSystem = window.NekoGameSystem || (window.NekoGameSystem = {});
  gameSystem.GameAudioSystem = GameAudioSystem;
})();
