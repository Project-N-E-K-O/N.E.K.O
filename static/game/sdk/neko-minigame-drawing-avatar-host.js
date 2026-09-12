/**
 * Trusted Drawing Guess Avatar provider for the N.E.K.O mini-game SDK.
 *
 * Character configuration, renderer globals and the shared speech analyser
 * stay on this side of the boundary. The game receives only bounded public
 * character descriptors and SDK Avatar controllers.
 */
(function (global) {
  'use strict';

  const SLOT = 'drawing-guess-character';
  const CHARACTER_LIMIT = 256;
  const QUERY_LIMIT = 4;
  const NAME_LIMIT = 128;
  const PATH_LIMIT = 2048;
  const VRM_DEFAULT_IDLE = '/static/vrm/animation/wait03.vrma.gz';
  const TYPES = Object.freeze(['live2d', 'vrm', 'mmd', 'pngtuber']);
  const PNG_IMAGE_KEYS = Object.freeze([
    'idle_image', 'talking_image', 'drag_image', 'click_image',
    'happy_image', 'sad_image', 'angry_image', 'surprised_image',
  ]);
  const LAYERS = Object.freeze({
    live2d: 'live2d-container',
    vrm: 'vrm-container',
    mmd: 'mmd-container',
    pngtuber: 'pngtuber-container',
  });

  class DrawingAvatarHostError extends Error {
    constructor(code, message, details = {}) {
      super(message);
      this.name = 'NekoMiniGameDrawingAvatarHostError';
      this.code = String(code || 'avatar_host_error');
      this.details = details && typeof details === 'object' ? details : {};
    }
  }

  function fail(code, message, details) {
    throw new DrawingAvatarHostError(code, message, details);
  }

  function cleanString(value, maximum = PATH_LIMIT) {
    if (typeof value !== 'string') return '';
    const text = value.trim();
    if (!text || text.length > maximum || ['undefined', 'null'].includes(text.toLowerCase())) return '';
    return text;
  }

  function hasOwn(value, key) {
    return Boolean(value) && typeof value === 'object'
      && Object.prototype.hasOwnProperty.call(value, key);
  }

  function firstOwnValue(candidates) {
    for (const [source, key] of candidates) {
      if (hasOwn(source, key)) return source[key];
    }
    return undefined;
  }

  function animationPaths(value) {
    return Object.freeze((Array.isArray(value) ? value : [value])
      .slice(0, 16)
      .map((item) => cleanString(item))
      .filter(Boolean));
  }

  function comparableMotionPath(value) {
    return cleanString(value).replace(/\\/g, '/');
  }

  function motionFile(definition) {
    return comparableMotionPath(definition?.File || definition?.file);
  }

  function configuredMotionIndex(definitions, configuredPath) {
    if (!Array.isArray(definitions)) return -1;
    const expected = comparableMotionPath(configuredPath);
    if (!expected) return -1;
    const exactIndex = definitions.findIndex((definition) => motionFile(definition) === expected);
    if (exactIndex >= 0) return exactIndex;
    const basename = expected.split('/').pop().toLowerCase();
    const basenameMatches = definitions
      .map((definition, index) => ({ index, path: motionFile(definition) }))
      .filter((entry) => entry.path && entry.path.split('/').pop().toLowerCase() === basename);
    return basenameMatches.length === 1 ? basenameMatches[0].index : -1;
  }

  function ensureLive2DPreviewMotionGroup(modelConfig, configuredPath) {
    if (!comparableMotionPath(configuredPath)) return;
    if (!modelConfig.FileReferences || typeof modelConfig.FileReferences !== 'object'
        || Array.isArray(modelConfig.FileReferences)) modelConfig.FileReferences = {};
    const fileReferences = modelConfig.FileReferences;
    if (!fileReferences.Motions || typeof fileReferences.Motions !== 'object'
        || Array.isArray(fileReferences.Motions)) fileReferences.Motions = {};
    if (!Array.isArray(fileReferences.Motions.PreviewAll)) {
      fileReferences.Motions.PreviewAll = [];
    }
    if (configuredMotionIndex(fileReferences.Motions.PreviewAll, configuredPath) < 0) {
      fileReferences.Motions.PreviewAll.push({ File: comparableMotionPath(configuredPath) });
    }
  }

  function boundedNumber(value, minimum, maximum, fallback) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return fallback;
    return Math.max(minimum, Math.min(maximum, numeric));
  }

  function normalizeView(value = {}) {
    return Object.freeze({
      scale: boundedNumber(value.scale, 0.5, 5000, 100),
      x: boundedNumber(value.x, -5000, 5000, 0),
      y: boundedNumber(value.y, -5000, 5000, 0),
    });
  }

  function reservedAvatar(character) {
    if (!character || typeof character !== 'object' || Array.isArray(character)) return {};
    const reserved = character._reserved;
    if (!reserved || typeof reserved !== 'object' || Array.isArray(reserved)) return {};
    const avatar = reserved.avatar;
    return avatar && typeof avatar === 'object' && !Array.isArray(avatar) ? avatar : {};
  }

  function safeLighting(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const output = {};
    for (const key of [
      'ambient', 'main', 'fill', 'rim', 'top', 'bottom',
      'exposure', 'toneMapping', 'outlineWidthScale',
    ]) {
      const numeric = Number(value[key]);
      if (Number.isFinite(numeric)) output[key] = Math.max(-100, Math.min(100, numeric));
    }
    return Object.freeze(output);
  }

  function safePngConfig(character, avatar) {
    const nested = avatar.pngtuber && typeof avatar.pngtuber === 'object'
      && !Array.isArray(avatar.pngtuber) ? avatar.pngtuber : {};
    const legacy = character?.pngtuber && typeof character.pngtuber === 'object'
      && !Array.isArray(character.pngtuber) ? character.pngtuber : {};
    const source = { ...legacy, ...nested };
    const result = {};
    for (const key of PNG_IMAGE_KEYS) {
      const path = cleanString(source[key]);
      if (path) result[key] = path;
    }
    const metadata = cleanString(source.layered_metadata || source.metadata);
    if (metadata) result.layered_metadata = metadata;
    const adapter = cleanString(source.adapter, 64);
    if (adapter) result.adapter = adapter;
    result.mirror = source.mirror === true;
    return Object.freeze(result);
  }

  function rawAvatarConfig(name, character) {
    const avatar = reservedAvatar(character);
    const live2d = avatar.live2d && typeof avatar.live2d === 'object'
      && !Array.isArray(avatar.live2d) ? avatar.live2d : {};
    const legacyLive2d = character?.avatar?.live2d && typeof character.avatar.live2d === 'object'
      && !Array.isArray(character.avatar.live2d) ? character.avatar.live2d : {};
    const vrm = avatar.vrm && typeof avatar.vrm === 'object'
      && !Array.isArray(avatar.vrm) ? avatar.vrm : {};
    const mmd = avatar.mmd && typeof avatar.mmd === 'object'
      && !Array.isArray(avatar.mmd) ? avatar.mmd : {};
    const pngtuber = safePngConfig(character, avatar);
    const modelType = cleanString(character?.model_type, 32)
      || cleanString(avatar.model_type, 32)
      || 'live2d';
    const live3dSubType = cleanString(character?.live3d_sub_type, 32)
      || cleanString(avatar.live3d_sub_type, 32);
    const modelPath = cleanString(character?.model_path);
    let live2dPath = cleanString(character?.live2d) || cleanString(live2d.model_path);
    let vrmPath = cleanString(character?.vrm) || cleanString(vrm.model_path);
    let mmdPath = cleanString(character?.mmd) || cleanString(mmd.model_path);
    let pngPath = cleanString(pngtuber.idle_image)
      || cleanString(character?.pngtuber_idle_image)
      || (typeof character?.pngtuber === 'string' ? cleanString(character.pngtuber) : '')
      || (['pngtuber', 'png', 'png-tuber'].includes(modelType.toLowerCase()) ? modelPath : '');
    const type = modelType.toLowerCase();
    const subtype = live3dSubType.toLowerCase();
    if (!vrmPath && (type === 'vrm' || (type === 'live3d' && subtype === 'vrm'))) vrmPath = modelPath;
    if (!mmdPath && (type === 'mmd' || (type === 'live3d' && subtype === 'mmd'))) mmdPath = modelPath;
    if (!live2dPath && type === 'live2d') live2dPath = modelPath;
    let effective = type;
    if (effective === 'live3d') effective = subtype === 'mmd' ? 'mmd' : 'vrm';
    if (!effective || effective === 'default') {
      effective = pngPath ? 'pngtuber' : (vrmPath ? 'vrm' : 'live2d');
    }
    if (effective === 'png' || effective === 'png-tuber') effective = 'pngtuber';
    if (!TYPES.includes(effective)) effective = 'live2d';
    const paths = { live2d: live2dPath, vrm: vrmPath, mmd: mmdPath, pngtuber: pngPath };
    if (effective === 'vrm' && !paths.vrm) paths.vrm = '/static/vrm/sister1.0.vrm';
    if (effective === 'mmd' && !paths.mmd) paths.mmd = '/static/mmd/Miku/Miku.pmx';
    const live2dIdleAnimations = animationPaths(firstOwnValue([
      [live2d, 'idle_animation'],
      [character, 'live2d_idle_animation'],
      [legacyLive2d, 'idle_animation'],
    ]));
    const vrmIdleAnimations = animationPaths(firstOwnValue([
      [vrm, 'idle_animation'],
      [character, 'idle_animation'],
      [character, 'idleAnimations'],
      [character, 'idleAnimation'],
    ]));
    const mmdIdleAnimations = animationPaths(firstOwnValue([
      [mmd, 'idle_animation'],
      [character, 'mmd_idle_animations'],
      [character, 'mmd_idle_animation'],
    ]));
    return {
      name,
      type: effective,
      path: cleanString(paths[effective]),
      paths: Object.freeze({ ...paths }),
      pngtuber,
      lighting: safeLighting(firstOwnValue([
        [vrm, 'lighting'],
        [character, 'lighting'],
      ])),
      live2dIdleAnimation: live2dIdleAnimations[0] || '',
      idleAnimation: vrmIdleAnimations[0] || '',
      idleAnimations: vrmIdleAnimations,
      mmdIdleAnimations,
    };
  }

  function create(options = {}) {
    const windowImpl = options.windowImpl || global;
    const documentImpl = options.documentImpl || windowImpl.document;
    const fetchImpl = options.fetchImpl || windowImpl.fetch?.bind(windowImpl);
    const avatarRuntime = options.avatarRuntime || windowImpl.NekoMiniGameAvatarHost;
    const slot = cleanString(options.slot, 64) || SLOT;
    const containerId = cleanString(options.containerId, 128) || 'model-stage';
    const mmdContainerId = cleanString(options.mmdContainerId, 128) || LAYERS.mmd;
    const mmdCanvasId = cleanString(options.mmdCanvasId, 128) || 'mmd-canvas';
    const pngContainerId = cleanString(options.pngContainerId, 128) || LAYERS.pngtuber;
    const layers = options.extendedOnly
      ? { mmd: mmdContainerId, pngtuber: pngContainerId } : LAYERS;
    if (!avatarRuntime || typeof avatarRuntime.create !== 'function') {
      fail('invalid_host', 'The trusted mini-game Avatar runtime is unavailable');
    }
    if (typeof fetchImpl !== 'function') fail('invalid_host', 'A trusted fetch implementation is required');

    const privateDescriptorsByName = new Map();
    const lifetime = new (windowImpl.AbortController || AbortController)();
    const queries = new Set();
    let disposed = false;

    async function query(requestOptions, invoke) {
      if (disposed) fail('disposed', 'The Avatar host has been disposed');
      if (requestOptions.signal?.aborted) fail('cancelled', 'Avatar query cancelled');
      if (queries.size >= QUERY_LIMIT) fail('busy', 'Avatar query limit reached');
      const requestedTimeout = requestOptions.timeoutMs ?? 10000;
      if (!Number.isFinite(requestedTimeout) || requestedTimeout <= 0) {
        fail('invalid_timeout', 'Avatar query timeout must be positive and finite');
      }
      const timeoutMs = Math.min(30000, Math.max(1, Math.floor(requestedTimeout)));
      const controller = new (windowImpl.AbortController || AbortController)();
      let reason = '';
      let cancel;
      const cancelled = new Promise((_, reject) => {
        cancel = (code) => {
          if (reason) return;
          reason = code;
          controller.abort();
          reject(new DrawingAvatarHostError(code, 'Avatar query cancelled'));
        };
      });
      const onAbort = () => cancel('cancelled');
      const onDispose = () => cancel('disposed');
      requestOptions.signal?.addEventListener('abort', onAbort, { once: true });
      lifetime.signal.addEventListener('abort', onDispose, { once: true });
      const timer = windowImpl.setTimeout(() => cancel('timeout'), timeoutMs);
      queries.add(controller);
      // Caller cancellation is prompt, but ignored aborts keep their raw slot
      // until actual settlement so repeated timeouts cannot accumulate work.
      const raw = Promise.resolve().then(() => {
        if (controller.signal.aborted) fail(reason, 'Avatar query cancelled');
        return invoke({ signal: controller.signal });
      }).finally(() => queries.delete(controller));
      try {
        const result = await Promise.race([raw, cancelled]);
        if (controller.signal.aborted) fail(reason, 'Avatar query cancelled');
        return result;
      } finally {
        windowImpl.clearTimeout(timer);
        requestOptions.signal?.removeEventListener('abort', onAbort);
        lifetime.signal.removeEventListener('abort', onDispose);
      }
    }

    async function json(url, requestOptions = {}) {
      const controller = new (windowImpl.AbortController || AbortController)();
      const signals = [lifetime.signal, requestOptions.signal].filter(Boolean);
      const abort = () => controller.abort();
      for (const signal of signals) {
        if (signal.aborted) abort();
        else signal.addEventListener('abort', abort, { once: true });
      }
      // Managed queries and mounts already own a total deadline. A second
      // per-fetch timer would silently shorten that caller's 10–30s budget.
      const timer = requestOptions.signal ? null : windowImpl.setTimeout(abort, 10000);
      try {
        if (controller.signal.aborted) fail('cancelled', 'Avatar request cancelled');
        const response = await fetchImpl(url, {
          cache: 'no-store', credentials: 'same-origin', ...requestOptions, signal: controller.signal,
        });
        if (controller.signal.aborted) fail('cancelled', 'Avatar request cancelled');
        if (!response?.ok) fail('request_failed', 'The Avatar character request failed', {
          status: Number(response?.status || 0),
        });
        const value = await response.json();
        if (controller.signal.aborted) fail('cancelled', 'Avatar request cancelled');
        return value;
      } catch (cause) {
        if (controller.signal.aborted) fail('cancelled', 'Avatar request cancelled');
        throw cause;
      } finally {
        if (timer !== null) windowImpl.clearTimeout(timer);
        for (const signal of signals) signal.removeEventListener('abort', abort);
      }
    }

    function loadCharacters(requestOptions) {
      // Each query owns its request; cancelling one consumer must not abort
      // another consumer or leave it attached to an abandoned shared promise.
      return json('/api/characters', requestOptions).then((payload) => {
        const raw = payload?.['猫娘'];
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return Object.freeze({});
        const result = Object.create(null);
        for (const [rawName, value] of Object.entries(raw).slice(0, CHARACTER_LIMIT)) {
          const name = cleanString(rawName, NAME_LIMIT);
          if (name && value && typeof value === 'object' && !Array.isArray(value)) result[name] = value;
        }
        return Object.freeze(result);
      });
    }

    async function currentCharacterName(requestOptions) {
      const payload = await json('/api/characters/current_catgirl', requestOptions);
      return cleanString(payload?.current_catgirl, NAME_LIMIT);
    }

    async function resolveLive2DPath(name, fallback, requestOptions) {
      if (!name) return fallback;
      try {
        const payload = await json(
          `/api/characters/current_live2d_model?catgirl_name=${encodeURIComponent(name)}`,
          requestOptions,
        );
        const resolved = payload?.success ? cleanString(payload?.model_info?.path) : '';
        return resolved || fallback;
      } catch (cause) {
        if (requestOptions.signal.aborted || cause?.code === 'cancelled') throw cause;
        return fallback;
      }
    }

    function publicDescriptor(descriptor) {
      if (!descriptor) return null;
      const model = descriptor.path
        ? Object.freeze({ type: descriptor.type, path: descriptor.path })
        : null;
      return Object.freeze({
        name: descriptor.name,
        model,
        rendererAvailable: Boolean(model),
        fallbackModels: Object.freeze(TYPES.filter(type => type !== descriptor.type)
          .map(type => ({ type, path: descriptor.paths[type] }))
          .filter(model => model.path).map(Object.freeze)),
      });
    }

    function trustedDescriptorForModel(characterName, model) {
      const name = cleanString(characterName, NAME_LIMIT);
      const descriptor = name ? privateDescriptorsByName.get(name) : null;
      const type = cleanString(model?.type, 32).toLowerCase();
      const path = cleanString(model?.path);
      const configuredPath = descriptor?.paths[type];
      if (!descriptor || !TYPES.includes(type) || !path || !configuredPath
          || configuredPath !== path) {
        fail('model_not_allowed', 'Avatar model is not the trusted character model', {
          characterName: name,
        });
      }
      return Object.freeze({ ...descriptor, type, path });
    }

    function getCharacter(name = '', requestOptions = {}) {
      return query(requestOptions, (managed) => readCharacter(name, managed));
    }

    async function readCharacter(name, requestOptions) {
      if (disposed) fail('disposed', 'The Drawing Guess Avatar host has been disposed');
      const requested = cleanString(name, NAME_LIMIT) || await currentCharacterName(requestOptions);
      if (requestOptions.signal.aborted) fail('cancelled', 'Avatar query cancelled');
      if (!requested) return null;
      const characters = await loadCharacters(requestOptions);
      if (requestOptions.signal.aborted) fail('cancelled', 'Avatar query cancelled');
      const character = Object.prototype.hasOwnProperty.call(characters, requested)
        ? characters[requested]
        : null;
      if (!character) return null;
      const configured = rawAvatarConfig(requested, character);
      const configuredMmd = cleanString(configured.paths.mmd).replace(/\\/g, '/');
      if (configuredMmd && !/^(https?:\/\/|\/)/.test(configuredMmd)) {
        // The browser cannot decide which filesystem owns a relative model.
        // Use the existing game-independent character projection, not a
        // guessed static prefix, for both primary and fallback MMD models.
        const resolved = await json(
          `/api/game/sdk-avatar/character?lanlan_name=${encodeURIComponent(requested)}`, requestOptions,
        );
        if (resolved?.lanlan_name !== requested) fail('invalid_response', 'Avatar character identity changed');
        const path = cleanString(resolved?.mmd_path);
        configured.paths = Object.freeze({ ...configured.paths, mmd: path });
        if (configured.type === 'mmd') configured.path = path;
      }
      if (configured.type === 'live2d' || configured.paths.live2d) {
        // An optional fallback is only advertised when canonical resolution
        // succeeds. Preserve the existing primary Live2D compatibility path.
        const path = await resolveLive2DPath(requested,
          configured.type === 'live2d' ? configured.paths.live2d : '', requestOptions);
        configured.paths = Object.freeze({ ...configured.paths, live2d: path });
        if (configured.type === 'live2d') configured.path = path;
      }
      const descriptor = Object.freeze({
        ...configured,
        path: cleanString(configured.path),
      });
      if (disposed) fail('disposed', 'The Avatar host was disposed during character lookup');
      if (requestOptions.signal.aborted) fail('cancelled', 'Avatar query cancelled');
      if (!privateDescriptorsByName.has(descriptor.name) && privateDescriptorsByName.size >= CHARACTER_LIMIT) {
        privateDescriptorsByName.delete(privateDescriptorsByName.keys().next().value);
      }
      privateDescriptorsByName.set(descriptor.name, descriptor);
      return publicDescriptor(descriptor);
    }

    function listCharacters(requestOptions = {}) {
      return query(requestOptions, async (managed) => {
        const characters = await loadCharacters(managed);
        return Object.freeze(Object.keys(characters).slice(0, CHARACTER_LIMIT));
      });
    }

    function waitForRuntime(predicate, readyEvent, failedEvent, label, signal, timeoutMs = 10000) {
      if (signal?.aborted) {
        return Promise.reject(new DrawingAvatarHostError(
          'disposed', `${label} renderer load was cancelled`, { type: label },
        ));
      }
      if (predicate()) return Promise.resolve();
      return new Promise((resolve, reject) => {
        let settled = false;
        let timer = null;
        const cleanup = () => {
          if (timer) windowImpl.clearTimeout(timer);
          if (readyEvent) windowImpl.removeEventListener?.(readyEvent, onReady);
          if (failedEvent) windowImpl.removeEventListener?.(failedEvent, onFailed);
          signal?.removeEventListener?.('abort', onAbort);
        };
        const finish = (error) => {
          if (settled) return;
          settled = true;
          cleanup();
          if (error) reject(error); else resolve();
        };
        const onReady = () => { if (predicate()) finish(); };
        const onFailed = () => finish(new DrawingAvatarHostError(
          'renderer_unavailable', `${label} failed to initialize`, { type: label },
        ));
        const onAbort = () => finish(new DrawingAvatarHostError(
          'disposed', `${label} renderer load was cancelled`, { type: label },
        ));
        if (readyEvent) windowImpl.addEventListener?.(readyEvent, onReady);
        if (failedEvent) windowImpl.addEventListener?.(failedEvent, onFailed);
        signal?.addEventListener?.('abort', onAbort, { once: true });
        timer = windowImpl.setTimeout(() => {
          if (predicate()) finish();
          else finish(new DrawingAvatarHostError(
            'renderer_unavailable', `${label} timed out`, { type: label },
          ));
        }, timeoutMs);
      });
    }

    function setLayer(kind) {
      for (const [candidate, id] of Object.entries(layers)) {
        const node = documentImpl?.getElementById?.(id);
        if (!node) continue;
        const hidden = candidate !== kind;
        node.hidden = hidden;
        node.classList?.toggle?.('hidden', hidden);
        if (node.style) node.style.display = hidden ? 'none' : '';
      }
    }

    function suppressChrome(manager) {
      if (!manager) return;
      manager.setupFloatingButtons = function () {};
      manager.setupHTMLLockIcon = function () {};
      manager.setFullscreenTrackingEnabled = function () {};
      manager.enableMouseTracking = function () {};
      manager.setupDragAndDrop = function (model) {
        if (model) model.interactive = false;
      };
    }

    function analyser() {
      return windowImpl.appState?.globalAnalyser || windowImpl.globalAnalyser || null;
    }

    function createController({ config, viewport, signal }) {
      const speechAnalyser = avatarRuntime.createSpeechAnalyser();
      const characterName = cleanString(config?.characterName, NAME_LIMIT);
      const descriptor = trustedDescriptorForModel(characterName, config?.model);
      const state = {
        kind: '',
        manager: null,
        descriptor,
        model: null,
        view: normalizeView(),
        viewport,
        fit: config.fit || {},
        layout: null,
        nativeLive2DScale: null,
        imageCleanup: null,
        baseViewport: null,
        ready: false,
        paused: false,
        speaking: false,
        mouthFrame: null,
        mouthParameterId: '',
        disposed: false,
        disposePromise: null,
        modelGeneration: 0,
        characterName,
      };
      const managerDisposals = new WeakMap();

      function loadIsActive(generation) {
        return !state.disposed && !signal?.aborted && generation === state.modelGeneration;
      }

      function ensureLoadActive(generation) {
        if (!loadIsActive(generation)) {
          fail('disposed', 'Avatar renderer load was cancelled');
        }
      }

      async function retireIfStale(manager, kind, generation) {
        if (loadIsActive(generation)) return;
        if (state.manager === manager) state.manager = null;
        await disposeRenderer(kind, manager);
        fail('disposed', 'Avatar renderer load was cancelled');
      }

      function live2dModel() {
        const manager = state.manager;
        if (!manager) return null;
        try { return manager.getCurrentModel?.() || manager.currentModel || null; }
        catch (_) { return manager.currentModel || null; }
      }

      function stopLive2DMouth() {
        if (state.mouthFrame != null) {
          windowImpl.cancelAnimationFrame?.(state.mouthFrame);
          state.mouthFrame = null;
        }
        const core = live2dModel()?.internalModel?.coreModel;
        if (core && state.mouthParameterId && typeof core.setParameterValueById === 'function') {
          try { core.setParameterValueById(state.mouthParameterId, 0); } catch (_) { /* renderer retired */ }
        }
      }

      function stopSpeaking() {
        state.speaking = false;
        speechAnalyser.clear();
        stopLive2DMouth();
        const manager = state.manager;
        if (state.kind === 'vrm') {
          try { manager?.animation?.stopLipSync?.(); } catch (_) { /* renderer retired */ }
        } else if (state.kind === 'mmd') {
          try { manager?.animationModule?.stopLipSync?.(); } catch (_) { /* renderer retired */ }
        } else if (state.kind === 'pngtuber') {
          try { manager?.setSpeaking?.(false); } catch (_) { /* renderer retired */ }
        }
      }

      function clearManagerTimer(manager, property, interval = false) {
        const timer = manager?.[property];
        if (timer == null) return;
        try {
          const clear = interval
            ? (windowImpl.clearInterval || windowImpl.clearTimeout)
            : windowImpl.clearTimeout;
          clear?.call(windowImpl, timer);
        } catch (_) { /* timer already retired */ }
        manager[property] = null;
      }

      function removeManagerWindowListener(manager, property, eventName) {
        const handler = manager?.[property];
        if (!handler) return;
        try { windowImpl.removeEventListener?.(eventName, handler); }
        catch (_) { /* listener already retired */ }
        manager[property] = null;
      }

      async function disposeLive2DManager(manager) {
        manager._activeLoadToken = Number(manager._activeLoadToken || 0) + 1;
        try { manager.pixi_app?.ticker?.stop?.(); } catch (_) { /* renderer already retired */ }
        try { await manager.removeModel?.({ skipCloseWindows: true }); }
        catch (_) { /* continue with the hard cleanup below */ }
        try { manager.cleanupEventListeners?.(); }
        catch (_) { /* continue with explicit listener cleanup */ }
        try { manager._stopIdleFpsGovernor?.(); }
        catch (_) { /* continue with explicit timer cleanup */ }

        removeManagerWindowListener(manager, '_screenChangeHandler', 'resize');
        removeManagerWindowListener(manager, '_displayChangeHandler', 'electron-display-changed');
        for (const property of [
          'motionTimer', '_idleFpsRestoreTimer', '_reinstallTimer', '_canvasRevealTimer',
          '_savePositionDebounceTimer', '_snapCheckTimer', '_clickEffectRestoreTimer',
          '_hideButtonsTimer', 'tutorialProtectionTimer',
        ]) clearManagerTimer(manager, property);
        for (const property of ['_idleFpsGovernorTimer', '_savedParamsTimer']) {
          clearManagerTimer(manager, property, true);
        }
        if (manager._idleMotionLoopTimers
            && typeof manager._idleMotionLoopTimers[Symbol.iterator] === 'function'
            && typeof manager._idleMotionLoopTimers.clear === 'function') {
          for (const timer of manager._idleMotionLoopTimers) {
            try { windowImpl.clearTimeout?.(timer); } catch (_) { /* timer already retired */ }
          }
          manager._idleMotionLoopTimers.clear();
        }
        if (manager._popupTimers && typeof manager._popupTimers === 'object') {
          for (const timer of Object.values(manager._popupTimers)) {
            try { windowImpl.clearTimeout?.(timer); } catch (_) { /* timer already retired */ }
          }
          manager._popupTimers = {};
        }

        try {
          if (typeof manager.dispose === 'function') await manager.dispose({ preserveView: true });
        } catch (_) { /* the hard cleanup below remains authoritative */ }

        const model = manager.currentModel;
        manager.currentModel = null;
        if (model) {
          try { model.removeAllListeners?.(); } catch (_) { /* model already retired */ }
          try { model.destroy?.({ children: true }); } catch (_) { /* model already retired */ }
        }
        const pixiApp = manager.pixi_app;
        manager.pixi_app = null;
        if (pixiApp) {
          try { pixiApp.ticker?.stop?.(); } catch (_) { /* ticker already retired */ }
          try {
            pixiApp.destroy?.(false, { children: true, texture: true, baseTexture: true });
          } catch (_) { /* renderer already retired */ }
        }
        manager.isInitialized = false;
        manager._initPIXIPromise = null;
      }

      function disposeRenderer(kind, manager) {
        if (!manager) return Promise.resolve();
        const existing = managerDisposals.get(manager);
        if (existing) return existing;
        const cleanup = (async () => {
          if (kind === 'live2d') await disposeLive2DManager(manager);
          else if (typeof manager.dispose === 'function') await manager.dispose();
          else if (manager.pixi_app?.destroy) await manager.pixi_app.destroy(false);
        })().catch((error) => {
          windowImpl.console?.warn?.('[DrawingAvatarHost] renderer cleanup failed', error);
        });
        managerDisposals.set(manager, cleanup);
        return cleanup;
      }

      function disposeManager() {
        state.imageCleanup?.();
        state.imageCleanup = null;
        if (state.disposePromise) return state.disposePromise;
        stopSpeaking();
        const manager = state.manager;
        const kind = state.kind;
        const referenceModel = kind === 'vrm' ? manager?.currentModel?.vrm?.scene : manager?.currentModel?.mesh;
        if (referenceModel) avatarRuntime.releasePerspectiveReference(referenceModel, manager.camera);
        state.manager = null;
        state.ready = false;
        if (!manager) return Promise.resolve();
        const cleanup = disposeRenderer(kind, manager);
        const tracked = cleanup.finally(() => {
          if (state.disposePromise === tracked) state.disposePromise = null;
        });
        state.disposePromise = tracked;
        return tracked;
      }

      function fitLive2D() {
        if (state.kind !== 'live2d' || !state.viewport) return;
        const manager = state.manager;
        const model = live2dModel();
        if (!manager?.pixi_app?.renderer || !model) return;
        const width = Math.max(1, Math.round(state.viewport.width));
        const height = Math.max(1, Math.round(state.viewport.height));
        manager.pixi_app.renderer.resize(width, height);
        const canvas = manager.pixi_app.view || manager.pixi_app.renderer.view;
        canvas?.style?.setProperty?.('width', `${width}px`, 'important');
        canvas?.style?.setProperty?.('height', `${height}px`, 'important');
        model.anchor?.set?.(0.5, 0.5);
        const bounds = model.getLocalBounds?.();
        if (!bounds?.width || !bounds?.height) return;
        if (state.nativeLive2DScale == null) state.nativeLive2DScale = Math.abs(model.scale.x) || 1;
        const native = state.nativeLive2DScale;
        const layout = avatarRuntime.fitRectangle({ width: bounds.width * native,
          height: bounds.height * native }, state.viewport, state.fit);
        const scale = native * layout.scale * state.view.scale / 100;
        model.scale?.set?.(scale);
        const aligned = avatarRuntime.fitRectangle({ width: bounds.width * scale,
          height: bounds.height * scale }, state.viewport,
          { ...state.fit, autoScale: false, scaleMultiplier: 1 });
        const rendered = model.getBounds?.();
        if (rendered?.width > 0 && rendered?.height > 0) {
          model.x += aligned.x - rendered.x;
          model.y += aligned.y - rendered.y;
        }
        model.x += width * (state.view.x / 100);
        model.y += height * (state.view.y / 100);
        state.layout = layout;
      }

      function fitPngtuber(manager) {
        if (state.disposed || !state.viewport || !manager?.image) return;
        const image = manager.image;
        const width = image.naturalWidth || image.width;
        const height = image.naturalHeight || image.height;
        if (!(width > 0 && height > 0)) return;
        const layout = avatarRuntime.fitRectangle({ width, height }, state.viewport, state.fit);
        const scaled = { width: layout.width * state.view.scale / 100,
          height: layout.height * state.view.scale / 100 };
        const aligned = avatarRuntime.fitRectangle(scaled, state.viewport,
          { ...state.fit, autoScale: false, scaleMultiplier: 1 });
        const bounce = manager.currentSpeakingBounceTransform?.() || {};
        const breathing = manager.currentLayeredBreathingTransform?.() || {};
        const hop = manager.currentTalkingHopTransform?.() || {};
        const sx = (manager.config?.mirror ? -1 : 1) * (bounce.scaleX || 1)
          * (breathing.scaleX || 1) * (hop.scaleX || 1);
        const sy = (bounce.scaleY || 1) * (breathing.scaleY || 1) * (hop.scaleY || 1);
        Object.assign(image.style, {
          position: 'absolute', left: `${aligned.x + state.viewport.width * state.view.x / 100}px`,
          top: `${aligned.y + state.viewport.height * state.view.y / 100}px`, right: 'auto', bottom: 'auto',
          width: `${scaled.width}px`, height: `${scaled.height}px`, maxWidth: 'none', maxHeight: 'none',
          transformOrigin: 'center bottom',
          transform: `translateY(${(bounce.y || 0) + (breathing.y || 0) + (hop.y || 0)}px) scale(${sx}, ${sy})`,
        });
        state.layout = layout;
      }

      function observeImage(manager) {
        const image = manager.imageElement;
        if (!image?.addEventListener || manager.image !== image) return Promise.resolve();
        return new Promise((resolve, reject) => {
          let waiting = true;
          let timer = null;
          const finish = (error) => {
            if (!waiting) return;
            waiting = false;
            if (timer !== null) windowImpl.clearTimeout(timer);
            timer = null;
            signal?.removeEventListener('abort', onAbort);
            if (error) reject(error); else resolve();
          };
          const onLoad = () => { fitPngtuber(manager); finish(); };
          const onError = () => finish(new DrawingAvatarHostError('renderer_unavailable', 'Avatar image failed to load'));
          const onAbort = () => finish(new DrawingAvatarHostError('disposed', 'Avatar image load cancelled'));
          image.addEventListener('load', onLoad);
          image.addEventListener('error', onError);
          signal?.addEventListener('abort', onAbort, { once: true });
          // One listener pair per controller, retained for talking/emotion image
          // changes; one initial timer, released on success/error/cancel/dispose.
          state.imageCleanup = () => {
            image.removeEventListener('load', onLoad);
            image.removeEventListener('error', onError);
            onAbort();
          };
          timer = windowImpl.setTimeout(onError, 15000);
          if (signal?.aborted) onAbort();
          else if (image.complete) {
            if (image.naturalWidth > 0) onLoad(); else onError();
          }
        });
      }

      function fitRenderer() {
        if (!state.viewport || !state.manager) return;
        const manager = state.manager;
        const layer = documentImpl.getElementById(layers[state.kind]);
        if (layer?.style) Object.assign(layer.style, {
          position: 'absolute', left: '0', top: '0',
          width: `${state.viewport.width}px`, height: `${state.viewport.height}px`, overflow: 'hidden',
        });
        if (state.kind === 'live2d') fitLive2D();
        else if (state.kind === 'pngtuber') fitPngtuber(manager);
        else {
          manager.onWindowResize?.();
          manager.renderer?.setSize?.(state.viewport.width, state.viewport.height);
          manager.effect?.setSize?.(state.viewport.width, state.viewport.height);
          const model = state.kind === 'mmd' ? manager.currentModel?.mesh : manager.currentModel?.vrm?.scene;
          if (model && manager.camera) {
            state.layout = avatarRuntime.fitPerspectiveModel(windowImpl.THREE, model, manager.camera,
              state.viewport, state.fit, state.view);
          }
        }
      }

      async function restoreLive2DIdle(manager, descriptor, generation) {
        const configuredPath = comparableMotionPath(descriptor?.live2dIdleAnimation);
        if (!configuredPath) return;
        const loadedModel = manager.getCurrentModel?.() || manager.currentModel;
        const motionManager = loadedModel?.internalModel?.motionManager;
        const definitions = motionManager?.definitions || motionManager?._definitions;
        const motionIndex = configuredMotionIndex(definitions?.PreviewAll, configuredPath);
        const matchedPath = motionIndex >= 0
          ? motionFile(definitions.PreviewAll[motionIndex]) : '';
        const basename = (matchedPath || configuredPath).split('/').pop();

        // loadModel suppresses its combined Idle expression+motion path below.
        // Restore only the expression without racing the configured motion, and
        // keep this optional network-backed work off the renderer-ready gate.
        try {
          const expressionTask = manager.playExpression?.('Idle');
          expressionTask?.catch?.((error) => windowImpl.console?.warn?.(
            '[Drawing Avatar] Live2D idle expression failed:', error,
          ));
        } catch (error) {
          windowImpl.console?.warn?.('[Drawing Avatar] Live2D idle expression failed:', error);
        }

        try {
          if (!loadedModel || typeof loadedModel.motion !== 'function'
              || !motionManager || typeof motionManager.loadMotion !== 'function'
              || motionIndex < 0) {
            throw new Error('Configured Live2D idle motion is unavailable for this model');
          }
          if (!motionManager.motionGroups && !motionManager._motionGroups) {
            motionManager.motionGroups = {};
          }
          const motionGroups = motionManager.motionGroups || motionManager._motionGroups;
          if (!Array.isArray(motionGroups.PreviewAll)) motionGroups.PreviewAll = [];
          try { manager._clearIdleMotionLoopTimers?.(); } catch (_) { /* optional scheduler */ }
          const loadedMotion = await motionManager.loadMotion('PreviewAll', motionIndex);
          await retireIfStale(manager, 'live2d', generation);

          const motion = motionGroups.PreviewAll?.[motionIndex] || loadedMotion;
          if (!motion) throw new Error('Configured Live2D idle motion could not be loaded');
          if (typeof motion.setIsLoop === 'function') motion.setIsLoop(true);
          else if (motion._loop !== undefined) motion._loop = true;
          manager._userIdleAnimations = [basename];
          if (manager.hasActiveActionMotion?.(loadedModel)) {
            try { manager.setupIdleMotionLoop?.(loadedModel); } catch (_) { /* optional scheduler */ }
            return;
          }

          const motionState = motionManager.state;
          if ((Number(motionState?.currentPriority || 0) === 1
              || motionState?.reservedIdleGroup !== undefined)
              && typeof motionManager.stopAllMotions === 'function') {
            motionManager.stopAllMotions();
          }
          const started = await loadedModel.motion('PreviewAll', motionIndex, 1);
          await retireIfStale(manager, 'live2d', generation);
          if (started === false) throw new Error('Configured Live2D idle motion did not start');
          try { manager.setupIdleMotionLoop?.(loadedModel); } catch (_) { /* optional scheduler */ }
        } catch (error) {
          // A stale loader must still be retired; a missing optional motion must not discard the model.
          await retireIfStale(manager, 'live2d', generation);
          manager._userIdleAnimations = [];
          try { manager.setupIdleMotionLoop?.(loadedModel); } catch (_) { /* default idle remains optional */ }
          windowImpl.console?.warn?.('[Drawing Avatar] Live2D idle animation failed:', error);
        }
      }

      async function loadLive2D(model, descriptor, generation) {
        await waitForRuntime(
          () => typeof windowImpl.Live2DManager === 'function' && Boolean(windowImpl.PIXI?.live2d),
          null, null, 'live2d', signal,
        );
        ensureLoadActive(generation);
        const modelConfig = await json(model.path, { signal });
        ensureLoadActive(generation);
        modelConfig.url = model.path;
        ensureLive2DPreviewMotionGroup(modelConfig, descriptor?.live2dIdleAnimation);
        const manager = new windowImpl.Live2DManager();
        state.manager = manager;
        suppressChrome(manager);
        const initialized = typeof manager.ensurePIXIReady === 'function'
          ? manager.ensurePIXIReady('live2d-canvas', 'live2d-container', {
            backgroundAlpha: 0, antialias: true,
            resizeMode: 'fixed', width: state.viewport.width, height: state.viewport.height,
          })
          : manager.initPIXI('live2d-canvas', 'live2d-container', {
            backgroundAlpha: 0, antialias: true,
            resizeMode: 'fixed', width: state.viewport.width, height: state.viewport.height,
          });
        await initialized;
        await retireIfStale(manager, 'live2d', generation);
        suppressChrome(manager);
        await manager.loadModel(modelConfig, {
          isMobile: false,
          skipCloseWindows: true,
          suppressPersistentExpressions: true,
          suppressInitialIdle: Boolean(descriptor?.live2dIdleAnimation),
        });
        await retireIfStale(manager, 'live2d', generation);
        await restoreLive2DIdle(manager, descriptor, generation);
        await retireIfStale(manager, 'live2d', generation);
        manager.pixi_app?.ticker?.start?.();
      }

      async function loadVrm(model, descriptor, generation) {
        await waitForRuntime(
          () => Boolean(windowImpl.vrmModuleLoaded) && typeof windowImpl.VRMManager === 'function',
          'vrm-modules-ready', 'vrm-modules-failed', 'vrm', signal,
        );
        ensureLoadActive(generation);
        const manager = new windowImpl.VRMManager();
        state.manager = manager;
        suppressChrome(manager);
        const path = typeof windowImpl.convertVRMModelPath === 'function'
          ? windowImpl.convertVRMModelPath(model.path) : model.path;
        const ok = await manager.initThreeJS('vrm-canvas', 'vrm-container', descriptor?.lighting || null,
          { embed: true, resizeMode: 'fixed' });
        await retireIfStale(manager, 'vrm', generation);
        if (ok === false) fail('renderer_unavailable', 'VRM scene initialization failed');
        suppressChrome(manager);
        await manager.loadModel(path, {
          canvasId: 'vrm-canvas',
          containerId: 'vrm-container',
          embed: true,
          // Never let this isolated renderer borrow another character's global idle motion.
          idleAnimation: descriptor?.idleAnimation || VRM_DEFAULT_IDLE,
          idleAnimations: descriptor?.idleAnimations || undefined,
        });
        await retireIfStale(manager, 'vrm', generation);
        await avatarRuntime.preparePerspectiveReference(windowImpl.THREE, manager, {
          type: 'vrm', signal, isCurrent: () => !state.disposed && state.modelGeneration === generation,
        });
        await retireIfStale(manager, 'vrm', generation);
        const presentationIdle = descriptor?.idleAnimation || VRM_DEFAULT_IDLE;
        if (presentationIdle !== VRM_DEFAULT_IDLE && typeof manager.playVRMAAnimation === 'function') {
          try {
            await manager.playVRMAAnimation(presentationIdle, { loop: true, immediate: true, isIdle: true,
              shouldApply: () => !state.disposed && state.modelGeneration === generation });
            await retireIfStale(manager, 'vrm', generation);
          } catch (error) {
            await retireIfStale(manager, 'vrm', generation);
            windowImpl.console?.warn?.('[Drawing Avatar] VRM presentation idle failed:', error);
          }
        }
      }

      async function loadMmd(model, descriptor, generation) {
        await waitForRuntime(
          () => Boolean(windowImpl.mmdModuleLoaded) && typeof windowImpl.MMDManager === 'function',
          'mmd-modules-ready', 'mmd-modules-failed', 'mmd', signal,
        );
        ensureLoadActive(generation);
        const manager = new windowImpl.MMDManager();
        state.manager = manager;
        suppressChrome(manager);
        if (typeof windowImpl.fetchMMDConfig === 'function') {
          try { await windowImpl.fetchMMDConfig(); } catch (_) { /* defaults remain usable */ }
          await retireIfStale(manager, 'mmd', generation);
        }
        const path = typeof windowImpl._mmdConvertPath === 'function'
          ? windowImpl._mmdConvertPath(model.path) : model.path;
        if (!manager.core?.renderer) {
          await manager.init(mmdCanvasId, mmdContainerId, { embed: true });
          await retireIfStale(manager, 'mmd', generation);
        }
        let savedSettings = null;
        try {
          const settingsData = await json(
            `/api/characters/catgirl/${encodeURIComponent(descriptor.name)}/mmd_settings`,
            { signal },
          );
          await retireIfStale(manager, 'mmd', generation);
          if (settingsData?.success && settingsData.settings
              && typeof settingsData.settings === 'object'
              && !Array.isArray(settingsData.settings)) {
            savedSettings = settingsData.settings;
            const physics = savedSettings.physics;
            if (physics && typeof physics === 'object' && !Array.isArray(physics)) {
              if (physics.enabled != null) manager.enablePhysics = physics.enabled === true;
              if (physics.strength != null && Number.isFinite(Number(physics.strength))) {
                manager.physicsStrength = boundedNumber(physics.strength, 0.1, 2.0, 1.0);
              }
            }
          }
        } catch (error) {
          // Saved settings are optional, but cancellation must still retire this renderer.
          await retireIfStale(manager, 'mmd', generation);
          windowImpl.console?.warn?.('[Drawing Avatar] MMD settings request failed:', error);
        }
        suppressChrome(manager);
        await manager.loadModel(path, { embed: true });
        await retireIfStale(manager, 'mmd', generation);
        if (savedSettings && typeof manager.applySettings === 'function') {
          const nonPhysicsSettings = { lighting: savedSettings.lighting,
            rendering: savedSettings.rendering, cursorFollow: savedSettings.cursorFollow };
          try {
            await manager.applySettings(nonPhysicsSettings);
            await retireIfStale(manager, 'mmd', generation);
          } catch (error) {
            // A bad optional appearance setting must not discard a usable model.
            await retireIfStale(manager, 'mmd', generation);
            windowImpl.console?.warn?.('[Drawing Avatar] MMD settings apply failed:', error);
          }
        }
        const previousIdle = manager.currentAnimationUrl;
        await avatarRuntime.preparePerspectiveReference(windowImpl.THREE, manager, {
          type: 'mmd', signal, isCurrent: () => !state.disposed && state.modelGeneration === generation,
        });
        await retireIfStale(manager, 'mmd', generation);
        const idleAnimation = descriptor?.mmdIdleAnimations?.[0] || previousIdle;
        if (idleAnimation && typeof manager.loadAnimation === 'function') {
          try {
            await manager.loadAnimation(idleAnimation, { immediate: true });
            await retireIfStale(manager, 'mmd', generation);
            manager.playAnimation?.('idle');
          } catch (error) {
            // A missing/broken optional motion must not discard a usable model.
            // Cancellation is different: retireIfStale disposes and rethrows it.
            await retireIfStale(manager, 'mmd', generation);
            windowImpl.console?.warn?.('[Drawing Avatar] MMD idle animation failed:', error);
          }
        }
      }

      async function loadPngtuber(model, descriptor, generation) {
        await waitForRuntime(
          () => typeof windowImpl.PNGTuberManager === 'function',
          null, null, 'pngtuber', signal,
        );
        ensureLoadActive(generation);
        const manager = new windowImpl.PNGTuberManager(pngContainerId);
        state.manager = manager;
        const applyTransform = manager.applyTransform?.bind(manager);
        manager.applyTransform = (...args) => {
          applyTransform?.(...args);
          fitPngtuber(manager);
        };
        suppressChrome(manager);
        const config = { ...(descriptor?.pngtuber || {}) };
        if (!config.idle_image) config.idle_image = model.path;
        await manager.load(config);
        await retireIfStale(manager, 'pngtuber', generation);
        manager.detachSpeechListeners?.();
        manager.detachDragListeners?.();
        manager.detachLayeredHotkeys?.();
        manager.detachLayeredPlayEvent?.();
        manager.cleanupFloatingButtons?.();
        manager.clearLayeredTimers?.();
        manager.setSpeaking?.(false);
        manager.setState?.('idle');
        manager.show?.();
        await observeImage(manager);
        await retireIfStale(manager, 'pngtuber', generation);
      }

      async function setModel(model) {
        if (state.disposed) fail('disposed', 'Avatar controller has been disposed');
        const type = cleanString(model?.type, 32).toLowerCase();
        const path = cleanString(model?.path);
        if (!TYPES.includes(type) || !path) fail('invalid_request', 'Avatar model is invalid');
        if (!state.descriptor
            || state.descriptor.type !== type || state.descriptor.path !== path) {
          fail('model_not_allowed', 'Avatar controller cannot change its trusted character binding');
        }
        const generation = ++state.modelGeneration;
        await disposeManager();
        ensureLoadActive(generation);
        state.kind = type;
        state.model = Object.freeze({ type, path });
        state.baseViewport = null;
        state.nativeLive2DScale = null;
        state.layout = null;
        state.mouthParameterId = '';
        setLayer(type);
        try {
          if (type === 'live2d') await loadLive2D(state.model, state.descriptor, generation);
          else if (type === 'vrm') await loadVrm(state.model, state.descriptor, generation);
          else if (type === 'mmd') await loadMmd(state.model, state.descriptor, generation);
          else await loadPngtuber(state.model, state.descriptor, generation);
          ensureLoadActive(generation);
          state.ready = true;
          await raw.setView(state.view);
          if (state.paused) raw.pause();
        } catch (error) {
          await disposeManager();
          throw error;
        }
      }

      function beginLive2DMouth(audioAnalyser = analyser()) {
        const core = live2dModel()?.internalModel?.coreModel;
        if (!audioAnalyser || !core || typeof core.setParameterValueById !== 'function') return false;
        const candidates = ['ParamMouthOpenY', 'ParamMouthOpen', 'ParamA', 'ParamO'];
        state.mouthParameterId = candidates.find((id) => {
          try {
            return typeof core.getParameterIndex !== 'function' || Number(core.getParameterIndex(id)) >= 0;
          } catch (_) { return false; }
        }) || '';
        if (!state.mouthParameterId) return false;
        const data = new Uint8Array(audioAnalyser.fftSize || audioAnalyser.frequencyBinCount || 2048);
        let mouth = 0;
        const animate = () => {
          if (!state.speaking || state.kind !== 'live2d' || state.disposed) return;
          try { audioAnalyser.getByteTimeDomainData(data); }
          catch (_) { stopLive2DMouth(); return; }
          let sum = 0;
          for (let index = 0; index < data.length; index += 1) {
            const sample = (data[index] - 128) / 128;
            sum += sample * sample;
          }
          const target = Math.min(1, Math.sqrt(sum / Math.max(1, data.length)) * 10);
          mouth = mouth * 0.55 + target * 0.45;
          try { core.setParameterValueById(state.mouthParameterId, mouth); }
          catch (_) { stopLive2DMouth(); return; }
          state.mouthFrame = windowImpl.requestAnimationFrame?.(animate) ?? null;
        };
        animate();
        return true;
      }

      const raw = {
        setModel,
        async setSpeechPlayback(frame) {
          if (state.disposed) return false;
          if (!frame?.active || !frame.mouthFrame || state.paused || !state.ready) {
            stopSpeaking();
            return false;
          }
          // Keep an existing lip-sync loop attached; only replace its bounded
          // samples. There is no per-frame timer or analyser allocation.
          const alreadyAutomatic = state.speaking && state.automaticSpeech;
          if (!alreadyAutomatic) stopSpeaking();
          if (!speechAnalyser.update(frame.mouthFrame)) {
            stopSpeaking();
            return false;
          }
          state.automaticSpeech = true;
          if (alreadyAutomatic) return true;
          state.speaking = true;
          if (state.kind === 'live2d') state.speaking = beginLive2DMouth(speechAnalyser);
          else {
            const target = state.kind === 'vrm' ? state.manager?.animation
              : state.kind === 'mmd' ? state.manager?.animationModule : state.manager;
            const method = state.kind === 'pngtuber' ? 'setSpeaking' : 'startLipSync';
            if (typeof target?.[method] !== 'function') state.speaking = false;
            else target[method](state.kind === 'pngtuber' ? true : speechAnalyser);
          }
          return state.speaking;
        },
        async setView(value) {
          if (state.disposed) fail('disposed', 'Avatar controller has been disposed');
          state.view = normalizeView(value);
          fitRenderer();
          return state.view;
        },
        async setSpeaking(active) {
          if (typeof active !== 'boolean') fail('invalid_request', 'Avatar speaking state must be boolean');
          if (state.disposed) fail('disposed', 'Avatar controller has been disposed');
          stopSpeaking();
          state.automaticSpeech = false;
          if (!active || !state.ready || state.paused) return false;
          state.speaking = true;
          const audioAnalyser = analyser();
          if (state.kind === 'live2d') {
            const started = beginLive2DMouth();
            if (!started) state.speaking = false;
            return started;
          }
          if (state.kind === 'vrm') {
            if (!audioAnalyser || !state.manager?.animation?.startLipSync) {
              state.speaking = false;
              return false;
            }
            state.manager.animation.startLipSync(audioAnalyser);
          } else if (state.kind === 'mmd') {
            if (!audioAnalyser || !state.manager?.animationModule?.startLipSync) {
              state.speaking = false;
              return false;
            }
            state.manager.animationModule.startLipSync(audioAnalyser);
          } else if (state.kind === 'pngtuber') {
            if (!state.manager?.setSpeaking) {
              state.speaking = false;
              return false;
            }
            state.manager.setSpeaking(true);
          }
          return true;
        },
        focus(point) {
          if (state.disposed) fail('disposed', 'Avatar controller has been disposed');
          if (!state.viewport) return false;
          const x = boundedNumber(point?.x, -100000, 100000, state.viewport.width / 2);
          const y = boundedNumber(point?.y, -100000, 100000, state.viewport.height / 2);
          return raw.setView({
            ...state.view,
            x: (x / Math.max(1, state.viewport.width) - 0.5) * 100,
            y: (y / Math.max(1, state.viewport.height) - 0.5) * 100,
          });
        },
        setEmotion(name) {
          if (state.disposed) fail('disposed', 'Avatar controller has been disposed');
          const mood = cleanString(name, 64).toLowerCase() || 'idle';
          const rendererMood = ({
            idle: 'neutral', drawing: 'relaxed', thinking: 'relaxed', guessing: 'relaxed',
            talking: 'happy', happy: 'happy', sad: 'sad', angry: 'angry',
            surprised: 'surprised',
          })[mood] || 'neutral';
          if (state.kind === 'live2d') {
            const emotion = ({
              idle: 'Idle', drawing: 'thinking', thinking: 'thinking', guessing: 'thinking',
              talking: 'happy', happy: 'happy',
            })[mood] || 'Idle';
            if (!state.manager?.isEmotionChanging) {
              return Promise.resolve(state.manager?.setEmotion?.(emotion)).catch(() => undefined);
            }
          } else if (state.kind === 'vrm') {
            return state.manager?.expression?.setMood?.(rendererMood);
          } else if (state.kind === 'mmd') {
            return state.manager?.setEmotion?.(rendererMood);
          } else if (state.kind === 'pngtuber') {
            const pngState = ({ talking: 'talking', happy: 'happy' })[mood] || 'idle';
            return state.manager?.setState?.(pngState);
          }
          return undefined;
        },
        pause() {
          if (state.disposed) fail('disposed', 'Avatar controller has been disposed');
          state.paused = true;
          stopSpeaking();
          if (state.kind === 'live2d') state.manager?.pixi_app?.ticker?.stop?.();
          else state.manager?.pauseRendering?.();
        },
        resume() {
          if (state.disposed) fail('disposed', 'Avatar controller has been disposed');
          state.paused = false;
          if (state.kind === 'live2d') state.manager?.pixi_app?.ticker?.start?.();
          else state.manager?.resumeRendering?.();
        },
        getState() {
          return Object.freeze({
            kind: state.kind,
            ready: state.ready,
            paused: state.paused,
            speaking: state.speaking,
            view: state.view,
            layout: state.layout,
          });
        },
        async resize(viewport, fit = state.fit) {
          if (state.disposed) fail('disposed', 'Avatar controller has been disposed');
          state.viewport = viewport;
          state.fit = fit;
          fitRenderer();
        },
        dispose() {
          if (state.disposed) return state.disposePromise || Promise.resolve();
          state.disposed = true;
          state.modelGeneration += 1;
          return disposeManager();
        },
      };
      return raw;
    }

    const rendererHost = avatarRuntime.create({
      windowImpl,
      documentImpl,
      ResizeObserverImpl: options.ResizeObserverImpl || windowImpl.ResizeObserver,
      requestAnimationFrameImpl: options.requestAnimationFrameImpl,
      cancelAnimationFrameImpl: options.cancelAnimationFrameImpl,
      slots: {
        [slot]: {
          containerId,
          createController,
        },
      },
    });

    return Object.freeze({
      get activeCount() { return rendererHost.activeCount; },
      get pendingCount() { return rendererHost.pendingCount; },
      getCharacter,
      getCurrentCharacter(requestOptions = {}) { return getCharacter('', requestOptions); },
      listCharacters,
      async mount(config) {
        if (String(config?.slot || '') !== slot) {
          fail('slot_unavailable', 'The Drawing Guess Avatar slot is not registered');
        }
        let characterName = cleanString(config?.characterName, NAME_LIMIT);
        if (!characterName) {
          characterName = (await getCharacter('', { signal: config?.signal }))?.name || '';
        } else if (!privateDescriptorsByName.has(characterName)) {
          await getCharacter(characterName, { signal: config?.signal });
        }
        const descriptor = trustedDescriptorForModel(characterName, config?.model);
        const trustedConfig = Object.freeze({
          ...config,
          characterName: descriptor.name,
          model: Object.freeze({ type: descriptor.type, path: descriptor.path }),
        });
        return rendererHost.mount(trustedConfig);
      },
      dispose() {
        if (disposed) return Promise.resolve();
        disposed = true;
        lifetime.abort();
        privateDescriptorsByName.clear();
        return rendererHost.dispose();
      },
    });
  }

  global.NekoMiniGameDrawingAvatarHost = Object.freeze({
    create,
    Error: DrawingAvatarHostError,
  });
})(window);
