(function () {
  'use strict';

  // 游戏音频配置规则：
  // - 音频配置归各游戏自己维护，不做一个全项目游戏音频总配置文件。
  // - 各游戏使用统一配置形状：{ bgm: {...}, sfx: {...} }。
  // - loopedBgm 是可选的“命名循环 BGM 注册表”，只有需要 playLoopedBgm('a.b') 这种
  //   字符串路径调用时才放进配置；如果游戏自己在 bgm 下组织 { intro, loop, outro } 并
  //   直接传给 playLoopedBgm(config)，不需要额外写 loopedBgm。
  // - 游戏音频系统只负责播放注册资源；什么时候播、为什么播，由具体游戏代码决定。
  // - 具体游戏可以把配置单独放在 static/game/games/<gameType>/ 下，再由页面加载。
  // - 如果希望 VS Code 在写 gameAudioConfig.bgm. 时给下拉提示，游戏代码应直接引用
  //   该配置对象变量；只通过 window.xxx 动态读取时，补全能力会弱一些。
  // - 对外只暴露 NekoGameSystem.GameAudioSystem；BGM 播放器和音效播放器是内部实现，
  //   不作为游戏代码直接调用的公共入口。
  //
  // BGM 调优经验：
  // - 普通歌单适合菜单、短循环要求不高的背景音乐；循环 BGM 适合游戏战斗/比赛这种要长期
  //   持续播放的场景。
  // - 循环 BGM 播放顺序为 intro -> loop -> outro：intro 只进场一次，loop 负责比赛中持续
  //   循环，outro 只在 finishLoopedBgm() 收尾时播放。
  // - L->L 循环尽量使用浏览器原生 audio.loop，不要等 ended 后再由 JS 重新 play；后者容易
  //   产生可感知断点。
  // - A->B 切换依赖交叉淡入淡出降低突兀感；如果直接 stop 旧音频再 play 新音频，会出现
  //   空白段或“卡一下”的体感。
  // - 预加载可以减少首次读取、解码和创建音频对象带来的停顿，但 HTMLAudio 仍不保证采样级
  //   无缝；如果必须严格无缝，需要 Web Audio 预解码和精确调度。
  // - MP3 自身可能有编码延迟或头尾静音；素材的剪辑点比代码更能决定最终是否无缝。

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

  function readNonNegativeNumber(value, fallback = 1) {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) return fallback;
    return Math.max(0, numberValue);
  }

  function decibelsToMultiplier(value, fallback = 1) {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) return fallback;
    return 10 ** (numberValue / 20);
  }

  function normalizeVolumeMix(value = {}) {
    return {
      baseVolume: clamp01(value.baseVolume, 1),
      maxVolume: clamp01(value.maxVolume, 1),
    };
  }

  function resolveMixedVolume(userVolume, mix, track, options = {}, progress = 1) {
    const baseVolume = clamp01(mix?.baseVolume, 1);
    const maxVolume = clamp01(mix?.maxVolume, 1);
    const resourceMultiplier = readNonNegativeNumber(
      track?.volumeMultiplier ?? track?.volumeScale,
      1
    );
    const resourceGainMultiplier = Number.isFinite(Number(track?.computedGainMultiplier))
      ? readNonNegativeNumber(track.computedGainMultiplier, 1)
      : decibelsToMultiplier(track?.gainDb, 1);
    const playMultiplier = readNonNegativeNumber(
      options.volumeMultiplier ?? options.playMultiplier,
      1
    );
    const fadeProgress = clamp01(progress, 1);
    return Math.min(
      maxVolume,
      clamp01(userVolume, 1)
        * baseVolume
        * resourceGainMultiplier
        * resourceMultiplier
        * playMultiplier
        * fadeProgress
    );
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
    const normalized = { ...track, src };
    const gainDb = Number(normalized.gainDb);
    if (Number.isFinite(gainDb)) {
      normalized.computedGainMultiplier = decibelsToMultiplier(gainDb, 1);
    }
    return normalized;
  }

  function normalizeAudioList(value) {
    if (Array.isArray(value)) return value.map(normalizeTrack).filter(Boolean);
    const track = normalizeTrack(value);
    return track ? [track] : [];
  }

  function inheritTrackMixOptions(track, parent) {
    if (!track || !parent || typeof parent !== 'object') return track;
    const inherited = { ...track };
    if (inherited.gainDb === undefined && parent.gainDb !== undefined) {
      inherited.gainDb = parent.gainDb;
    }
    if (inherited.volumeMultiplier === undefined && parent.volumeMultiplier !== undefined) {
      inherited.volumeMultiplier = parent.volumeMultiplier;
    }
    if (inherited.volumeScale === undefined && parent.volumeScale !== undefined) {
      inherited.volumeScale = parent.volumeScale;
    }
    return inherited;
  }

  function normalizeLoopedBgmConfig(value) {
    if (!value || typeof value !== 'object') return null;
    const intro = inheritTrackMixOptions(normalizeTrack(value.intro), value);
    const loop = inheritTrackMixOptions(normalizeTrack(value.loop), value);
    const outro = inheritTrackMixOptions(normalizeTrack(value.outro), value);
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
      this.mix = normalizeVolumeMix(options.mix);

      this.volume = options.volume !== undefined
        ? clamp01(options.volume, DEFAULT_BGM_VOLUME)
        : (this.persistVolume ? readStoredVolume(this.storageKey, DEFAULT_BGM_VOLUME) : DEFAULT_BGM_VOLUME);
      this.currentAudio = null;
      this.fadingAudio = null;
      this.fadingTrack = null;
      this.fadingOptions = {};
      this.fadingProgress = 0;
      this.currentPlaylist = [];
      this.currentSignature = '';
      this.currentContentSignature = '';
      this.currentTrack = null;
      this.currentOptions = {};
      this.currentLoopPlaylist = this.loopPlaylist;
      this.lastTrackBySignature = new Map();
      this.preloadCache = new Map();
      this.fadeTimer = null;
      this.endWaiters = [];
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
     * @param {boolean} [options.repeat] 是否在当前普通 BGM 歌单结束后继续循环；默认使用播放器配置。
     * @param {boolean} [options.loop] repeat 的旧别名；避免和 playLoopedBgm 的 loop 段混用。
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
        this.currentOptions = {};
        this.stop();
        return Promise.resolve(false);
      }

      const samePlaylist = signature && signature === this.currentSignature;
      if (samePlaylist && this.currentAudio && !options.force) {
        return Promise.resolve(true);
      }

      this._resolveEndWaiters(false);
      this.currentPlaylist = normalized;
      this.currentSignature = signature;
      this.currentContentSignature = playlistSignature(normalized);
      this.currentOptions = { volumeMultiplier: options.volumeMultiplier ?? options.playMultiplier };
      const repeatOption = options.repeat === undefined ? options.loop : options.repeat;
      this.currentLoopPlaylist = repeatOption === undefined ? this.loopPlaylist : Boolean(repeatOption);
      this.pausedByUser = false;
      const nextTrack = this._pickTrack(normalized, signature);
      const result = this._crossfadeTo(nextTrack, options);
      if (this.currentLoopPlaylist) this._preloadNextTrack();
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
      this._applyVolume(this.currentAudio, 1, this.currentTrack, this.currentOptions);
      this._applyVolume(this.fadingAudio, this.fadingProgress, this.fadingTrack, this.fadingOptions);
      return this.volume;
    }

    setMix(mix = {}) {
      this.mix = normalizeVolumeMix(mix);
      this._applyVolume(this.currentAudio, 1, this.currentTrack, this.currentOptions);
      this._applyVolume(this.fadingAudio, this.fadingProgress, this.fadingTrack, this.fadingOptions);
    }

    pause() {
      this.pausedByUser = true;
      if (this.currentAudio) this.currentAudio.pause();
      if (this.fadingAudio) this.fadingAudio.pause();
    }

    resume() {
      this.pausedByUser = false;
      if (!this.currentAudio) return Promise.resolve(false);
      return this._safePlay(this.currentAudio);
    }

    stop(options = {}) {
      this.pendingPlayAfterUnlock = false;
      this._clearFadeTimer();
      disposeAudio(this.currentAudio);
      disposeAudio(this.fadingAudio);
      this.currentAudio = null;
      this.fadingAudio = null;
      this.fadingTrack = null;
      this.fadingOptions = {};
      this.fadingProgress = 0;
      this.currentTrack = null;
      this.currentOptions = {};
      if (options.notifyWaiters !== false) this._resolveEndWaiters(false);
    }

    destroy() {
      this.stop();
      for (const audio of this.preloadCache.values()) disposeAudio(audio);
      this.preloadCache.clear();
      this.destroyed = true;
      this.currentPlaylist = [];
      this.currentSignature = '';
      this.currentContentSignature = '';
      this.currentOptions = {};
      this.lastTrackBySignature.clear();
    }

    unlock() {
      if (!this.pendingPlayAfterUnlock || !this.currentAudio || this.pausedByUser) {
        return Promise.resolve(false);
      }
      return this._safePlay(this.currentAudio);
    }

    /**
     * 等待当前普通 BGM 自然播放结束。
     *
     * 主要用于一次性结算音乐：调用方可以先 playBgm(..., { repeat: false })，
     * 再等待它自然 ended。若 BGM 被 stop / destroy / 新 BGM 替换，则返回 false。
     *
     * @param {Object} [options] 等待选项。
     * @param {number} [options.timeoutMs] 最长等待毫秒数；不传则不设置超时。
     * @returns {Promise<boolean>} true 表示自然播完，false 表示被中断或超时。
     */
    waitForEnd(options = {}) {
      if (this.destroyed || !this.currentAudio) return Promise.resolve(false);
      return new Promise((resolve) => {
        const waiter = { resolve, timer: null };
        const timeoutMs = Math.max(0, Number(options.timeoutMs || 0) || 0);
        if (timeoutMs > 0) {
          waiter.timer = globalThis.setTimeout?.(() => {
            this._removeEndWaiter(waiter);
            resolve(false);
          }, timeoutMs) || null;
        }
        this.endWaiters.push(waiter);
      });
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
      const volumeOptions = {
        volumeMultiplier: options.volumeMultiplier ?? options.playMultiplier ?? this.currentOptions.volumeMultiplier,
      };

      this._clearFadeTimer();
      disposeAudio(this.fadingAudio);
      this.fadingAudio = null;
      this.fadingTrack = null;
      this.fadingOptions = {};
      this.fadingProgress = 0;
      const previousAudio = this.currentAudio;
      const previousTrack = this.currentTrack;
      const previousOptions = this.currentOptions;
      const nextAudio = this._createAudio(track);
      this.currentAudio = nextAudio;
      this.currentTrack = track;
      this.lastTrackBySignature.set(this.currentSignature, track);

      this._applyVolume(nextAudio, previousAudio ? 0 : 1, track, volumeOptions);

      const playPromise = this._safePlay(nextAudio);
      const fadeMs = Math.max(0, Number(options.fadeMs ?? this.fadeMs) || 0);
      if (!previousAudio || fadeMs <= 0) {
        disposeAudio(previousAudio);
        this._applyVolume(nextAudio, 1, track, volumeOptions);
        return playPromise;
      }

      this.fadingAudio = previousAudio;
      this._startCrossfade(previousAudio, nextAudio, fadeMs, previousTrack, previousOptions, track, volumeOptions);
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

    _startCrossfade(previousAudio, nextAudio, fadeMs, previousTrack, previousOptions, nextTrack, nextOptions) {
      const startedAt = Date.now();
      const previousStartVolume = Number(previousAudio.volume) || 0;
      const previousFullVolume = resolveMixedVolume(this.volume, this.mix, previousTrack, previousOptions, 1);
      const previousStartProgress = previousFullVolume > 0
        ? clamp01(previousStartVolume / previousFullVolume, 1)
        : (previousStartVolume > 0 ? 1 : 0);
      this.fadingTrack = previousTrack;
      this.fadingOptions = previousOptions || {};
      this.fadingProgress = previousStartProgress;

      this.fadeTimer = window.setInterval(() => {
        const elapsed = Date.now() - startedAt;
        const progress = Math.min(1, elapsed / fadeMs);
        this.fadingProgress = previousStartProgress * (1 - progress);
        this._applyVolume(previousAudio, this.fadingProgress, previousTrack, previousOptions);
        this._applyVolume(nextAudio, progress, nextTrack, nextOptions);

        if (progress >= 1) {
          this._clearFadeTimer();
          disposeAudio(previousAudio);
          if (this.fadingAudio === previousAudio) this.fadingAudio = null;
          this.fadingTrack = null;
          this.fadingOptions = {};
          this.fadingProgress = 0;
          this._applyVolume(nextAudio, 1, nextTrack, nextOptions);
        }
      }, 50);
    }

    _handleEnded(audio) {
      if (audio !== this.currentAudio || this.pausedByUser) return;
      if (!this.currentLoopPlaylist) {
        this._resolveEndWaiters(true);
        this.stop({ notifyWaiters: false });
        return;
      }
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

    _removeEndWaiter(waiter) {
      const index = this.endWaiters.indexOf(waiter);
      if (index >= 0) this.endWaiters.splice(index, 1);
      if (waiter.timer) globalThis.clearTimeout?.(waiter.timer);
    }

    _resolveEndWaiters(value) {
      const waiters = this.endWaiters.splice(0);
      for (const waiter of waiters) {
        if (waiter.timer) globalThis.clearTimeout?.(waiter.timer);
        waiter.resolve(Boolean(value));
      }
    }

    _applyVolume(audio, progress = 1, track = this.currentTrack, options = {}) {
      if (!audio) return;
      audio.volume = resolveMixedVolume(this.volume, this.mix, track, options, progress);
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
      this.mix = normalizeVolumeMix(options.mix);
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

    setMix(mix = {}) {
      this.mix = normalizeVolumeMix(mix);
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
      audio.volume = 0;
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
      const userVolume = options.volume === undefined
        ? this.volume
        : clamp01(options.volume, this.volume);
      audio.volume = resolveMixedVolume(userVolume, this.mix, track, options);
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
      this.mix = normalizeVolumeMix(options.mix);
      this.volume = options.volume !== undefined
        ? clamp01(options.volume, DEFAULT_BGM_VOLUME)
        : DEFAULT_BGM_VOLUME;
      this.currentAudio = null;
      this.currentConfig = null;
      this.currentId = '';
      this.currentContentSignature = '';
      this.currentTrack = null;
      this.currentOptions = {};
      this.phase = 'stopped';
      this.fadingAudio = null;
      this.fadingTrack = null;
      this.fadingOptions = {};
      this.fadingProgress = 0;
      this.pendingFinish = false;
      this.pendingPlayAfterUnlock = false;
      this.pausedByUser = false;
      this.destroyed = false;
      this.fadeTimer = null;
      this.preloadCache = new Map();
    }

    // 播放循环 BGM：
    // - intro：可选，只播放一次。
    // - loop：必填，循环段；没有收尾请求时使用浏览器原生 audio.loop 反复播放。
    // - outro：可选，finishLoopedBgm() 请求收尾后，等待当前 loop 轮次结束再播放。
    // - 切换到另一套循环 BGM 时保留旧音频做 crossfade，避免先停旧歌再开新歌造成空白。
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

      this.currentConfig = normalized;
      this.currentId = id;
      this.currentContentSignature = loopedBgmIdentityFromConfig(normalized);
      this.currentOptions = { volumeMultiplier: options.volumeMultiplier ?? options.playMultiplier };
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
      this._applyVolume(this.currentAudio, 1, this.currentTrack, this.currentOptions);
      this._applyVolume(this.fadingAudio, this.fadingProgress, this.fadingTrack, this.fadingOptions);
      return this.volume;
    }

    setMix(mix = {}) {
      this.mix = normalizeVolumeMix(mix);
      this._applyVolume(this.currentAudio, 1, this.currentTrack, this.currentOptions);
      this._applyVolume(this.fadingAudio, this.fadingProgress, this.fadingTrack, this.fadingOptions);
    }

    // 立即停止循环 BGM。用于强制退出页面、强制切换到普通 BGM 等场景。
    stop(options = {}) {
      this.pendingFinish = false;
      this.pendingPlayAfterUnlock = false;
      this._clearFadeTimer();
      disposeAudio(this.fadingAudio);
      this.fadingAudio = null;
      this.fadingTrack = null;
      this.fadingOptions = {};
      this.fadingProgress = 0;
      const audio = this.currentAudio;
      const track = this.currentTrack;
      const fadeOptions = this.currentOptions;
      this.currentAudio = null;
      this.currentTrack = null;
      this.currentConfig = null;
      this.currentId = '';
      this.currentContentSignature = '';
      this.currentOptions = {};
      this.phase = 'stopped';
      const fadeMs = Math.max(0, Number(options.fadeMs ?? 0) || 0);
      if (audio && fadeMs > 0) this._fadeOutAndDispose(audio, fadeMs, track, fadeOptions);
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
      if (this.phase === 'loop') {
        this.currentAudio.loop = false;
      }
      return Promise.resolve(true);
    }

    pause() {
      this.pausedByUser = true;
      if (this.currentAudio) this.currentAudio.pause();
      if (this.fadingAudio) this.fadingAudio.pause();
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
      disposeAudio(this.fadingAudio);
      this.fadingAudio = null;
      this.fadingTrack = null;
      this.fadingOptions = {};
      this.fadingProgress = 0;
      const previousAudio = this.currentAudio;
      const previousTrack = this.currentTrack;
      const previousOptions = this.currentOptions;
      const audio = this._createAudio(track, phase);
      this.currentAudio = audio;
      this.currentTrack = track;
      this.phase = phase;
      this._applyVolume(audio, previousAudio ? 0 : 1, track, this.currentOptions);
      const playPromise = this._safePlay(audio);

      const fadeMs = previousAudio ? this.fadeMs : 0;
      if (previousAudio && fadeMs > 0) {
        // 循环 BGM 之间切换时，这里是主要的抗停顿路径：
        // 新音频先启动，再把旧音频淡出。若改回先 stop 再 play，inGame -> max+angry
        // 这类切换会更容易出现可感知空白。
        this._startCrossfade(previousAudio, audio, fadeMs, previousTrack, previousOptions);
      } else {
        disposeAudio(previousAudio);
        this._applyVolume(audio, 1, track, this.currentOptions);
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
      // loop 段默认使用浏览器原生循环，避免每轮结束后再由 JS 重建音频造成明显断点。
      // 当 finishLoopedBgm() 请求收尾时，会把 loop 关掉，等待当前轮结束后再进入 outro。
      // 这里改善的是 L->L；L->E 仍要等 ended 事件再启动 outro，HTMLAudio 下不承诺严格无缝。
      audio.loop = phase === 'loop' && !this.pendingFinish;
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

    _applyVolume(audio, progress = 1, track = this.currentTrack, options = {}) {
      if (!audio) return;
      audio.volume = resolveMixedVolume(this.volume, this.mix, track, options, progress);
    }

    _startCrossfade(previousAudio, nextAudio, fadeMs, previousTrack, previousOptions) {
      this._clearFadeTimer();
      disposeAudio(this.fadingAudio);
      this.fadingAudio = previousAudio;
      this.fadingTrack = previousTrack;
      this.fadingOptions = previousOptions || {};
      const startedAt = Date.now();
      const previousStartVolume = Number(previousAudio.volume) || 0;
      const previousFullVolume = resolveMixedVolume(this.volume, this.mix, previousTrack, previousOptions, 1);
      const previousStartProgress = previousFullVolume > 0
        ? clamp01(previousStartVolume / previousFullVolume, 1)
        : (previousStartVolume > 0 ? 1 : 0);
      this.fadingProgress = previousStartProgress;
      this.fadeTimer = window.setInterval(() => {
        const elapsed = Date.now() - startedAt;
        const progress = Math.min(1, elapsed / fadeMs);
        this.fadingProgress = previousStartProgress * (1 - progress);
        this._applyVolume(previousAudio, this.fadingProgress, previousTrack, previousOptions);
        this._applyVolume(nextAudio, progress, this.currentTrack, this.currentOptions);
        if (progress >= 1) {
          this._clearFadeTimer();
          disposeAudio(this.fadingAudio);
          this.fadingAudio = null;
          this.fadingTrack = null;
          this.fadingOptions = {};
          this.fadingProgress = 0;
          this._applyVolume(nextAudio, 1, this.currentTrack, this.currentOptions);
        }
      }, 50);
    }

    _fadeOutAndDispose(audio, fadeMs, track, options) {
      disposeAudio(this.fadingAudio);
      this.fadingAudio = audio;
      this.fadingTrack = track;
      this.fadingOptions = options || {};
      const startedAt = Date.now();
      const startVolume = Number(audio.volume) || 0;
      const fullVolume = resolveMixedVolume(this.volume, this.mix, track, options, 1);
      const startProgress = fullVolume > 0
        ? clamp01(startVolume / fullVolume, 1)
        : (startVolume > 0 ? 1 : 0);
      this.fadingProgress = startProgress;
      this.fadeTimer = window.setInterval(() => {
        const elapsed = Date.now() - startedAt;
        const progress = Math.min(1, elapsed / fadeMs);
        this.fadingProgress = startProgress * (1 - progress);
        this._applyVolume(audio, this.fadingProgress, track, options);
        if (progress >= 1) {
          this._clearFadeTimer();
          if (this.fadingAudio === audio) this.fadingAudio = null;
          this.fadingTrack = null;
          this.fadingOptions = {};
          this.fadingProgress = 0;
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
      this.audioMix = {
        bgm: normalizeVolumeMix(options.bgmMix || options.audioMix?.bgm),
        sfx: normalizeVolumeMix(options.sfxMix || options.audioMix?.sfx),
      };
      this.bgm = new GameBgmPlayer({
        ...options,
        volume: options.bgmVolume ?? options.volume,
        mix: this.audioMix.bgm,
        storageKey: options.bgmStorageKey || DEFAULT_BGM_STORAGE_KEY,
        onError: options.onBgmError || options.onError,
      });
      this.loopedBgm = new GameLoopedBgmPlayer({
        ...options,
        volume: this.bgm.volume,
        mix: this.audioMix.bgm,
        onError: options.onLoopedBgmError || options.onBgmError || options.onError,
      });
      this.sfx = new GameSfxPlayer({
        ...options,
        volume: options.sfxVolume,
        mix: this.audioMix.sfx,
        storageKey: options.sfxStorageKey || DEFAULT_SFX_STORAGE_KEY,
        onError: options.onSfxError || options.onError,
      });
      if (options.config) this.configure(options.config);
    }

    /**
     * 注册游戏音频资源。
     *
     * @param {Object} config 配置对象。
     * @param {Object} [config.audioMix] 本游戏混音基准与上限配置。
     * @param {Object} [config.bgm] 普通 BGM 资源树。
     * @param {Object} [config.loopedBgm] 循环 BGM 资源树，叶子为 { intro?, loop, outro? }。
     * @param {Object} [config.sfx] 短音效资源树。
     * @returns {Object} 归一后的当前配置引用。
     */
    configure(config = {}) {
      this.config = {
        audioMix: config.audioMix || {},
        bgm: config.bgm || {},
        loopedBgm: config.loopedBgm || {},
        sfx: config.sfx || {},
      };
      this.setAudioMix(this.config.audioMix);
      return this.config;
    }

    /**
     * 设置本游戏混音基准。
     *
     * 音量条仍保存 0 到 1；audioMix 决定“音量条 100%”在本游戏里实际输出多大。
     * 单个资源没写 gainDb / volumeMultiplier 时默认不额外增益 / 衰减。
     *
     * @param {Object} [audioMix] 混音配置。
     * @param {Object} [audioMix.bgm] BGM 混音配置。
     * @param {number} [audioMix.bgm.baseVolume] BGM 基准音量，默认 1。
     * @param {number} [audioMix.bgm.maxVolume] BGM 最终上限，默认 1。
     * @param {Object} [audioMix.sfx] SFX 混音配置。
     * @param {number} [audioMix.sfx.baseVolume] SFX 基准音量，默认 1。
     * @param {number} [audioMix.sfx.maxVolume] SFX 最终上限，默认 1。
     */
    setAudioMix(audioMix = {}) {
      this.audioMix = {
        bgm: normalizeVolumeMix(audioMix.bgm),
        sfx: normalizeVolumeMix(audioMix.sfx),
      };
      this.bgm.setMix(this.audioMix.bgm);
      this.loopedBgm.setMix(this.audioMix.bgm);
      this.sfx.setMix(this.audioMix.sfx);
    }

    /**
     * 播放普通 BGM。
     *
     * @param {string|string[]|Object|Object[]} keyOrPlaylist 注册 key，或直接传入歌单 / 单个音频。
     * @param {Object} [options] 播放选项。
     * @param {string} [options.id] 直接传歌单时可指定身份；传 key 时默认用 key。
     * @param {boolean} [options.force] 是否强制重播同一套 BGM。
     * @param {number} [options.fadeMs] 本次切换淡入淡出时间。
     * @param {boolean} [options.repeat] 是否在当前普通 BGM 歌单结束后继续循环。
     * @param {boolean} [options.loop] repeat 的旧别名；避免和 playLoopedBgm 的 loop 段混用。
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
     * 等待当前普通 BGM 自然播放结束。
     *
     * 主要用于一次性结算音乐；循环 BGM 不会自然结束，调用方不要用它等待循环 BGM。
     *
     * @param {Object} [options] 等待选项。
     * @param {number} [options.timeoutMs] 最长等待毫秒数；不传则不设置超时。
     * @returns {Promise<boolean>} true 表示自然播完，false 表示被中断或超时。
     */
    waitForBgmEnd(options = {}) {
      return this.bgm.waitForEnd(options);
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
