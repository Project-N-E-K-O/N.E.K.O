/**
 * N.E.K.O Mini-Game Avatar host runtime.
 *
 * This module belongs to the trusted host side of the mini-game SDK. Games
 * request Avatar rendering through NekoMiniGame.avatar; they must not create
 * renderer listeners, ResizeObservers, or Live2D/VRM lifecycle objects
 * directly. Engine-specific adapters are registered by the N.E.K.O host.
 */
(function (global) {
  'use strict';

  const DEFAULT_RENDERER_LIMIT = 8;
  const MAX_RENDERER_LIMIT = 32;
  const DEFAULT_PENDING_OPERATION_LIMIT = 16;
  const MAX_PENDING_OPERATION_LIMIT = 64;
  const VIEWPORT_MODES = Object.freeze(['fixed', 'container', 'host-window']);
  const REQUIRED_CONTROLLER_METHODS = Object.freeze([
    'setModel', 'focus', 'setEmotion', 'pause', 'resume', 'getState', 'resize', 'dispose',
  ]);
  const live2dNativeBaselines = new WeakMap();
  const cameraNativeBaselines = new WeakMap();
  // One immutable reference per live model; never retains the model itself.
  const perspectiveReferences = new WeakMap();
  const disposedRawControllers = new WeakSet();

  // Read-only, bounded analyser facade for speech played in another window.
  // Frequency bins are real player samples; the time-domain facade preserves
  // only RMS amplitude for basic Live2D mouth opening, not recorded audio.
  function createSpeechAnalyser() {
    let frame = null;
    let expiresAt = 0;
    let context = Object.freeze({ sampleRate: 48000 });
    const current = () => Date.now() <= expiresAt ? frame : null;
    return Object.freeze({
      get frequencyBinCount() { return current()?.bins.length || 256; },
      get fftSize() { return this.frequencyBinCount * 2; },
      get context() { return context; },
      update(value) {
        if (!value || !Array.isArray(value.bins) || value.bins.length < 16
          || value.bins.length > 256 || (value.bins.length & (value.bins.length - 1)) !== 0
          || !value.bins.every(byte => Number.isInteger(byte) && byte >= 0 && byte <= 255)
          || !Number.isFinite(value.rms) || value.rms < 0 || value.rms > 1
          || !Number.isFinite(value.sampleRate) || value.sampleRate < 1000 || value.sampleRate > 192000) {
          frame = null;
          expiresAt = 0;
          return false;
        }
        frame = { bins: value.bins.slice(), rms: value.rms };
        context = Object.freeze({ sampleRate: value.sampleRate });
        expiresAt = Date.now() + 750;
        return true;
      },
      clear() { frame = null; expiresAt = 0; },
      getByteFrequencyData(target) {
        target.fill(0);
        const bins = current()?.bins;
        if (bins) target.set(bins.slice(0, target.length));
      },
      getByteTimeDomainData(target) {
        const amplitude = Math.min(127, Math.round((current()?.rms || 0) * 128));
        for (let i = 0; i < target.length; i++) target[i] = 128 + (i % 2 ? amplitude : -amplitude);
      },
    });
  }

  class NekoMiniGameAvatarHostError extends Error {
    constructor(code, message, details = {}) {
      super(message);
      this.name = 'NekoMiniGameAvatarHostError';
      this.code = String(code || 'avatar_host_error');
      this.details = details && typeof details === 'object' ? details : {};
    }
  }

  function fail(code, message, details) {
    throw new NekoMiniGameAvatarHostError(code, message, details);
  }

  function positiveInteger(value, fallback, maximum) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) return fallback;
    return Math.max(1, Math.min(Math.floor(numeric), maximum));
  }

  function viewportSize(width, height, mode) {
    const normalizedWidth = Number(width);
    const normalizedHeight = Number(height);
    if (!Number.isFinite(normalizedWidth) || normalizedWidth <= 0
        || !Number.isFinite(normalizedHeight) || normalizedHeight <= 0) {
      fail('viewport_unavailable', 'Avatar viewport has no usable size', {
        mode,
        width: normalizedWidth,
        height: normalizedHeight,
      });
    }
    return Object.freeze({
      mode,
      width: Math.min(normalizedWidth, 16384),
      height: Math.min(normalizedHeight, 16384),
    });
  }

  function normalizeAlignment(value) {
    const align = String(value || 'center');
    if (align === 'center') return ['center', 'center'];
    const parts = align.split('-');
    return parts.length === 2 ? parts : ['center', 'center'];
  }

  // CSS-pixel layout shared by image, Live2D and projected 3D bounds. No engine
  // units, game names, persistent caches or renderer-owned listeners here.
  function fitRectangle(size, viewport, fit = {}) {
    const width = Number(viewport?.width), height = Number(viewport?.height);
    const sourceWidth = Number(size?.width), sourceHeight = Number(size?.height);
    if (![width, height, sourceWidth, sourceHeight].every(n => Number.isFinite(n) && n > 0)) {
      fail('viewport_unavailable', 'Avatar bounds and viewport must have usable dimensions');
    }
    if (fit.autoScale !== undefined && typeof fit.autoScale !== 'boolean') {
      fail('invalid_request', 'Avatar fit.autoScale must be boolean');
    }
    const mode = fit.mode || 'contain';
    if (!['contain', 'cover', 'native', 'width', 'height'].includes(mode)) {
      fail('invalid_request', 'Avatar fit.mode is invalid');
    }
    const padding = Number(fit.padding ?? 0);
    const multiplier = Number(fit.scaleMultiplier ?? 1);
    const minWidth = Number(fit.minWidth ?? 0), minHeight = Number(fit.minHeight ?? 0);
    if (![padding, minWidth, minHeight].every(n => Number.isFinite(n) && n >= 0)
        || !Number.isFinite(multiplier) || multiplier <= 0) {
      fail('invalid_request', 'Avatar fit dimensions and multiplier must be finite and non-negative');
    }
    const availableWidth = Math.max(1, width - 2 * padding);
    const availableHeight = Math.max(1, height - 2 * padding);
    const widthRatio = availableWidth / sourceWidth, heightRatio = availableHeight / sourceHeight;
    const automatic = fit.autoScale !== false && mode !== 'native';
    let ratio = multiplier;
    if (automatic) {
      const maximum = mode === 'width' ? widthRatio : mode === 'height' ? heightRatio
        : mode === 'cover' ? Math.max(widthRatio, heightRatio) : Math.min(widthRatio, heightRatio);
      // A soft minimum must never override the chosen maximum/axis policy.
      ratio = Math.min(maximum, Math.max(maximum * multiplier,
        minWidth / sourceWidth, minHeight / sourceHeight));
    }
    const fittedWidth = sourceWidth * ratio, fittedHeight = sourceHeight * ratio;
    const [vertical, horizontal] = normalizeAlignment(fit.align);
    const x = horizontal === 'left' ? padding
      : horizontal === 'right' ? width - padding - fittedWidth : (width - fittedWidth) / 2;
    const y = vertical === 'top' ? padding
      : vertical === 'bottom' ? height - padding - fittedHeight : (height - fittedHeight) / 2;
    return Object.freeze({ width: fittedWidth, height: fittedHeight, x, y, scale: ratio,
      minimumSatisfied: !automatic || (fittedWidth + 1e-7 >= minWidth && fittedHeight + 1e-7 >= minHeight),
      clipped: x < 0 || y < 0 || x + fittedWidth > width + 1e-7 || y + fittedHeight > height + 1e-7 });
  }

  function capturePerspectiveReference(THREE, model, metadata = {}) {
    model.updateWorldMatrix(true, true);
    if (Math.abs(model.matrixWorld.determinant()) < 1e-12) {
      fail('invalid_renderer', 'Avatar reference root has a singular transform');
    }
    const inverseRoot = model.matrixWorld.clone().invert();
    const box = new THREE.Box3();
    const vertex = new THREE.Vector3();
    model.traverse(object => {
      const positions = object.geometry?.getAttribute?.('position');
      if (!positions) return;
      if (object.isInstancedMesh) fail('invalid_renderer', 'Instanced Avatar reference is unsupported');
      const toRoot = new THREE.Matrix4().multiplyMatrices(inverseRoot, object.matrixWorld);
      for (let i = 0; i < positions.count; i += 1) {
        // getVertexPosition includes morph targets and current skinning, unlike
        // cached geometry/SkinnedMesh.boundingBox. No mutation of engine bounds.
        if (object.isMesh) object.getVertexPosition(i, vertex);
        else vertex.fromBufferAttribute(positions, i);
        vertex.applyMatrix4(toRoot);
        if (![vertex.x, vertex.y, vertex.z].every(Number.isFinite)) {
          fail('invalid_renderer', 'Avatar reference contains non-finite vertices');
        }
        box.expandByPoint(vertex);
      }
    });
    if (box.isEmpty()) fail('invalid_renderer', 'Avatar model has empty bounds');
    const info = Object.freeze({ source: metadata.source || 'current-pose-fallback',
      reason: metadata.reason || 'reference-not-prepared',
      animation: metadata.animation || null,
      height: box.max.y - box.min.y });
    perspectiveReferences.set(model, { box, info });
    return info;
  }

  function releasePerspectiveReference(model, camera) {
    if (model) perspectiveReferences.delete(model);
    if (camera) cameraNativeBaselines.delete(camera);
  }

  async function preparePerspectiveReference(THREE, manager, options = {}) {
    const kind = options.type;
    if (kind !== 'vrm' && kind !== 'mmd') fail('invalid_renderer', 'Expected VRM or MMD reference');
    const loaded = manager.currentModel;
    const model = kind === 'vrm' ? loaded?.vrm?.scene : loaded?.mesh;
    if (!model) fail('invalid_renderer', 'Avatar model is not loaded');
    const current = () => !options.signal?.aborted && manager.currentModel === loaded
      && (typeof options.isCurrent !== 'function' || options.isCurrent());
    const check = () => { if (!current()) {
      releasePerspectiveReference(model, manager.camera);
      fail('disposed', 'Avatar reference preparation was cancelled');
    } };
    check();
    const fallback = capturePerspectiveReference(THREE, model, { reason: 'standing-reference-unavailable' });
    const visible = model.visible;
    model.visible = false;
    const animation = kind === 'vrm' ? '/static/vrm/animation/wait03.vrma.gz' : '/static/mmd/animation/wait03.vmd';
    try {
      if (kind === 'vrm') {
        const ok = await manager.playVRMAAnimation?.(animation,
          { loop: true, immediate: true, isIdle: true, shouldApply: current });
        check();
        if (ok !== true) return fallback;
        // Immediate playback evaluates the clip at t=0; update normalized bones
        // before reading raw skinned vertices. No sleep or physics time step.
        manager.animation?.update?.(0);
      } else {
        const clip = await manager.loadAnimation?.(animation, { immediate: true });
        check();
        if (!clip) return fallback;
        // MMD loadAnimation resolves after frame-zero mixer, IK and grant work.
        manager.playAnimation?.('idle');
      }
      model.updateWorldMatrix(true, true);
      const inverseRoot = model.matrixWorld.clone().invert();
      const bone = (vrmName, mmdName) => kind === 'vrm'
        ? loaded.vrm.humanoid?.getRawBoneNode?.(vrmName)
        : loaded.mesh.skeleton?.bones?.find(node => node.name === mmdName);
      const point = node => node?.getWorldPosition(new THREE.Vector3()).applyMatrix4(inverseRoot);
      // A named idle is not proof of a standing pose. Require both wrists below
      // upper arms and head above hips in this model's fixed local Y-up frame.
      const head = point(bone('head', '頭')), hips = point(bone('hips', '下半身'));
      const arms = [['leftUpperArm', '左腕', 'leftHand', '左手首'],
        ['rightUpperArm', '右腕', 'rightHand', '右手首']];
      const standing = head && hips && head.y > hips.y && arms.every(names => {
        const upper = point(bone(names[0], names[1])), hand = point(bone(names[2], names[3]));
        if (!upper || !hand) return false;
        const length = upper.distanceTo(hand);
        return length > 1e-6 && upper.y - hand.y >= length * 0.5;
      });
      check();
      if (!standing) return fallback;
      return capturePerspectiveReference(THREE, model,
        { source: 'standing-reference', reason: 'validated-arms-down', animation });
    } catch (error) {
      check();
      // Optional reference failure must not make an otherwise usable model fail.
      return fallback;
    } finally {
      if (current()) model.visible = visible;
    }
  }

  function fitPerspectiveModel(THREE, model, camera, viewport, fit = {}, view = {}) {
    if (!THREE?.Box3 || !model || !camera?.isPerspectiveCamera) {
      fail('invalid_renderer', 'Avatar 3D fit requires a model and perspective camera');
    }
    model.updateWorldMatrix(true, true);
    if (!perspectiveReferences.has(model)) capturePerspectiveReference(THREE, model);
    const reference = perspectiveReferences.get(model);
    const box = reference.box.clone().applyMatrix4(model.matrixWorld);
    if (box.isEmpty()) fail('invalid_renderer', 'Avatar model has empty bounds');
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    let baseline = cameraNativeBaselines.get(camera);
    if (!baseline || baseline.model !== model) {
      // Weak-key lifetime; only the current model is retained per live camera.
      baseline = { model, height: viewport.height, zoom: camera.zoom,
        distance: camera.position.distanceTo(center) || Math.max(size.x, size.y, size.z) * 2 };
      cameraNativeBaselines.set(camera, baseline);
    }
    const automatic = fit.autoScale !== false && fit.mode !== 'native';
    const distance = automatic ? Math.max(size.x, size.y, size.z, 0.001) * 2 : baseline.distance;
    camera.position.copy(center).add(new THREE.Vector3(0, 0, distance).applyQuaternion(camera.quaternion));
    camera.aspect = viewport.width / viewport.height;
    camera.zoom = automatic ? 1 : baseline.zoom * baseline.height / viewport.height;
    camera.clearViewOffset();
    camera.near = Math.max(0.00001, distance - size.length());
    camera.far = Math.max(camera.near + 1, distance + size.length() * 2);
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld(true);
    const corners = [];
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) {
      for (const z of [box.min.z, box.max.z]) corners.push(new THREE.Vector3(x, y, z));
    }
    const projected = () => {
      const points = corners.map(p => p.clone().project(camera));
      const left = (Math.min(...points.map(p => p.x)) + 1) * viewport.width / 2;
      const top = (1 - Math.max(...points.map(p => p.y))) * viewport.height / 2;
      return { x: left, y: top,
        width: (Math.max(...points.map(p => p.x)) - Math.min(...points.map(p => p.x))) * viewport.width / 2,
        height: (Math.max(...points.map(p => p.y)) - Math.min(...points.map(p => p.y))) * viewport.height / 2 };
    };
    const layout = fitRectangle(projected(), viewport, fit);
    camera.zoom *= layout.scale * (Number(view.scale ?? 100) / 100);
    camera.updateProjectionMatrix();
    const current = projected();
    const aligned = fitRectangle(current, viewport, { ...fit, autoScale: false, scaleMultiplier: 1 });
    const dx = aligned.x - current.x + viewport.width * Number(view.x || 0) / 100;
    const dy = aligned.y - current.y + viewport.height * Number(view.y || 0) / 100;
    camera.setViewOffset(viewport.width, viewport.height, -dx, -dy,
      viewport.width, viewport.height);
    return Object.freeze({ ...projected(), minimumSatisfied: layout.minimumSatisfied,
      reference: reference.info });
  }

  function fitLive2DModel(model, viewport, fit = {}) {
    if (!model || !model.scale || typeof model.scale.set !== 'function') {
      fail('invalid_renderer', 'A scalable Live2D model is required');
    }
    const width = Number(viewport?.width);
    const height = Number(viewport?.height);
    const modelWidth = Number(model.width);
    const modelHeight = Number(model.height);
    if (!(width > 0) || !(height > 0) || !(modelWidth > 0) || !(modelHeight > 0)) {
      fail('viewport_unavailable', 'Live2D model and viewport must have usable dimensions');
    }
    let baseline = live2dNativeBaselines.get(model);
    if (!baseline) {
      baseline = Object.freeze({ x: Number(model.scale.x), y: Number(model.scale.y),
        width: modelWidth, height: modelHeight });
      live2dNativeBaselines.set(model, baseline);
    }
    const layout = fitRectangle(baseline, viewport, fit);
    model.scale.set(baseline.x * layout.scale, baseline.y * layout.scale);
    const fittedWidth = Number(model.width);
    const fittedHeight = Number(model.height);
    const anchorX = Number(model.anchor?.x || 0);
    const anchorY = Number(model.anchor?.y || 0);
    model.x = layout.x + fittedWidth * anchorX;
    model.y = layout.y + fittedHeight * anchorY;
    return Object.freeze({
      width: fittedWidth,
      height: fittedHeight,
      x: model.x,
      y: model.y,
      scaleX: Number(model.scale.x),
      scaleY: Number(model.scale.y),
      minimumSatisfied: layout.minimumSatisfied,
    });
  }

  function create(options = {}) {
    const windowImpl = options.windowImpl || global;
    const documentImpl = options.documentImpl || windowImpl.document;
    const ResizeObserverImpl = options.ResizeObserverImpl || windowImpl.ResizeObserver;
    const requestFrame = options.requestAnimationFrameImpl
      || windowImpl.requestAnimationFrame?.bind(windowImpl)
      || ((callback) => windowImpl.setTimeout(callback, 16));
    const cancelFrame = options.cancelAnimationFrameImpl
      || windowImpl.cancelAnimationFrame?.bind(windowImpl)
      || ((id) => windowImpl.clearTimeout(id));
    const AbortControllerImpl = options.AbortControllerImpl
      || windowImpl.AbortController
      || global.AbortController;
    const rendererLimit = positiveInteger(
      options.rendererLimit,
      DEFAULT_RENDERER_LIMIT,
      MAX_RENDERER_LIMIT,
    );
    const pendingOperationLimit = positiveInteger(
      options.pendingOperationLimit,
      DEFAULT_PENDING_OPERATION_LIMIT,
      MAX_PENDING_OPERATION_LIMIT,
    );
    const slotInputs = options.slots;
    if (!slotInputs || typeof slotInputs !== 'object' || Array.isArray(slotInputs)) {
      fail('invalid_host', 'Avatar host slots must be an object');
    }
    const slots = new Map(Object.entries(slotInputs));
    if (!slots.size || slots.size > MAX_RENDERER_LIMIT) {
      fail('invalid_host', 'Avatar host slot count is invalid', { limit: MAX_RENDERER_LIMIT });
    }
    for (const [slot, descriptor] of slots) {
      if (!descriptor || typeof descriptor.createController !== 'function') {
        fail('invalid_host', `Avatar slot "${slot}" requires createController`);
      }
    }

    const active = new Map();
    const pending = new Map();
    const hostWindowStates = new Set();
    let hostWindowResizeHandler = null;
    let disposed = false;

    function descriptorContainer(descriptor) {
      if (descriptor.container && typeof descriptor.container === 'object') {
        return descriptor.container;
      }
      const containerId = String(descriptor.containerId || '').trim();
      return containerId ? documentImpl?.getElementById?.(containerId) : null;
    }

    function clipViewport(descriptor, viewport) {
      const style = descriptorContainer(descriptor)?.style;
      if (!style?.getPropertyValue) return () => {};
      const values = { overflow: 'hidden' };
      if (viewport.mode === 'fixed') Object.assign(values, {
        width: `${viewport.width}px`, height: `${viewport.height}px`,
      });
      const saved = Object.entries(values).map(([key, value]) => {
        const old = style.getPropertyValue(key), priority = style.getPropertyPriority(key);
        style.setProperty(key, value, 'important');
        return { key, value, old, priority };
      });
      return () => {
        for (const { key, value, old, priority } of saved) {
          if (style.getPropertyValue(key) !== value) continue;
          if (old) style.setProperty(key, old, priority);
          else style.removeProperty(key);
        }
      };
    }

    function measureViewport(config, descriptor) {
      const mode = String(config?.viewport?.mode || config?.resize?.mode || '');
      if (!VIEWPORT_MODES.includes(mode) || config?.resize?.mode !== mode) {
        fail('invalid_request', 'Avatar viewport and resize modes must match', { mode });
      }
      if (mode === 'fixed') {
        return viewportSize(config.viewport.width, config.viewport.height, mode);
      }
      if (mode === 'host-window') {
        return viewportSize(windowImpl.innerWidth, windowImpl.innerHeight, mode);
      }
      const container = descriptorContainer(descriptor);
      if (!container) {
        fail('viewport_unavailable', 'Avatar container is unavailable', { slot: config.slot });
      }
      const rect = typeof container.getBoundingClientRect === 'function'
        ? container.getBoundingClientRect()
        : null;
      return viewportSize(
        container.clientWidth || rect?.width,
        container.clientHeight || rect?.height,
        mode,
      );
    }

    function observeAsyncFailure(result, operation) {
      if (result && typeof result.catch === 'function') {
        result.catch((error) => windowImpl.console?.error?.(
          `[NekoMiniGameAvatarHost] ${operation} failed`,
          error,
        ));
      }
    }

    function disposeRaw(raw, operation = 'dispose') {
      if (!raw || (typeof raw !== 'object' && typeof raw !== 'function')
          || disposedRawControllers.has(raw)) return;
      disposedRawControllers.add(raw);
      try { observeAsyncFailure(raw.dispose?.(), operation); }
      catch (error) { windowImpl.console?.error?.(`[NekoMiniGameAvatarHost] ${operation} failed`, error); }
    }

    function pendingMountDisposed(slot) {
      fail('disposed', 'Avatar host was disposed while mounting', { slot });
    }

    function racePendingMount(operation, pendingState, slot) {
      return Promise.race([
        Promise.resolve(operation),
        pendingState.disposal.then(() => pendingMountDisposed(slot)),
      ]);
    }

    function ensureController(raw, slot) {
      if (!raw || typeof raw !== 'object') {
        fail('invalid_renderer', `Avatar slot "${slot}" returned no controller`);
      }
      const missing = REQUIRED_CONTROLLER_METHODS.filter((method) => typeof raw[method] !== 'function');
      if (missing.length) {
        fail('invalid_renderer', `Avatar slot "${slot}" returned an incomplete controller`, { missing });
      }
      return raw;
    }

    async function resizeState(state, reason) {
      if (state.disposed || disposed) return;
      const viewport = measureViewport(state.config, state.descriptor);
      const sameSize = state.viewport
        && state.viewport.width === viewport.width
        && state.viewport.height === viewport.height;
      if (sameSize && reason !== 'model-changed' && reason !== 'mounted') return;
      await state.raw.resize(viewport, state.config.fit, Object.freeze({ reason }));
      if (!state.disposed && !disposed) state.viewport = viewport;
    }

    function enqueueStateOperation(state, operation, callback) {
      try {
        ensureState(state, operation);
        if (state.pendingOperations >= pendingOperationLimit) {
          fail('busy', `Avatar slot "${state.config.slot}" operation limit reached`, {
            operation,
            limit: pendingOperationLimit,
          });
        }
      } catch (error) {
        return Promise.reject(error);
      }
      state.pendingOperations += 1;
      const run = state.operationTail
        .catch(() => undefined)
        .then(() => {
          ensureState(state, operation);
          return callback();
        });
      const cancellable = Promise.race([
        run,
        state.operationDisposal.then(() => {
          fail('disposed', 'Avatar controller has been disposed', { operation });
        }),
      ]);
      const tracked = cancellable.finally(() => {
        state.pendingOperations = Math.max(0, state.pendingOperations - 1);
      });
      state.operationTail = tracked;
      return tracked;
    }

    function scheduleResize(state, reason) {
      if (state.disposed || disposed) return;
      state.queuedResizeReason = reason;
      if (state.resizeFrameId != null || state.resizeInFlight) return;
      state.resizeFrameId = requestFrame(() => {
        state.resizeFrameId = null;
        if (state.disposed || disposed) return;
        const queuedReason = state.queuedResizeReason || reason;
        state.queuedResizeReason = '';
        state.resizeInFlight = true;
        enqueueStateOperation(
          state,
          'resize',
          () => resizeState(state, queuedReason),
        )
          .catch((error) => windowImpl.console?.error?.(
            '[NekoMiniGameAvatarHost] resize failed',
            error,
          ))
          .finally(() => {
            state.resizeInFlight = false;
            if (state.queuedResizeReason && !state.disposed && !disposed) {
              scheduleResize(state, state.queuedResizeReason);
            }
          });
      });
    }

    function syncHostWindowListener() {
      if (hostWindowStates.size && !hostWindowResizeHandler) {
        hostWindowResizeHandler = () => {
          for (const state of Array.from(hostWindowStates)) {
            scheduleResize(state, 'host-window-resize');
          }
        };
        windowImpl.addEventListener?.('resize', hostWindowResizeHandler);
      } else if (!hostWindowStates.size && hostWindowResizeHandler) {
        windowImpl.removeEventListener?.('resize', hostWindowResizeHandler);
        hostWindowResizeHandler = null;
      }
    }

    function attachResizeLifecycle(state) {
      const mode = state.config.resize.mode;
      if (mode === 'container') {
        if (typeof ResizeObserverImpl !== 'function') {
          fail('capability_unavailable', 'ResizeObserver is required for container Avatar mode');
        }
        const container = descriptorContainer(state.descriptor);
        if (!container) {
          fail('viewport_unavailable', 'Avatar container is unavailable', { slot: state.config.slot });
        }
        state.resizeObserver = new ResizeObserverImpl(() => scheduleResize(state, 'container-resize'));
        state.resizeObserver.observe(container);
      } else if (mode === 'host-window') {
        hostWindowStates.add(state);
        syncHostWindowListener();
      }
    }

    function detachResizeLifecycle(state) {
      if (state.resizeFrameId != null) {
        cancelFrame(state.resizeFrameId);
        state.resizeFrameId = null;
      }
      state.queuedResizeReason = '';
      if (state.resizeObserver) {
        state.resizeObserver.disconnect();
        state.resizeObserver = null;
      }
      if (hostWindowStates.delete(state)) syncHostWindowListener();
    }

    function disposeState(state) {
      if (!state || state.disposed) return;
      state.disposed = true;
      state.resolveOperationDisposal?.();
      state.resolveOperationDisposal = null;
      active.delete(state.config.slot);
      detachResizeLifecycle(state);
      disposeRaw(state.raw, `${state.config.slot}.dispose`);
      state.restoreClip?.();
    }

    function ensureState(state, operation) {
      if (disposed) fail('disposed', 'Avatar host has been disposed', { operation });
      if (!state || state.disposed) fail('disposed', 'Avatar controller has been disposed', { operation });
    }

    function publicController(state) {
      return Object.freeze({
        get disposed() { return disposed || state.disposed; },
        // Trusted adapter forwarding: nested providers must resize their inner
        // controller rather than acknowledging an update without applying it.
        resize(viewport, fit = state.config.fit) {
          return enqueueStateOperation(state, 'resize', async () => {
            const next = viewportSize(viewport?.width, viewport?.height, 'fixed');
            const config = { ...state.config, viewport: next, fit, resize: { mode: 'fixed' } };
            await state.raw.resize(next, fit, Object.freeze({ reason: 'explicit' }));
            ensureState(state, 'resize');
            detachResizeLifecycle(state);
            state.restoreClip?.();
            state.restoreClip = clipViewport(state.descriptor, next);
            state.config = config;
            state.viewport = next;
          });
        },
        async setModel(model) {
          return enqueueStateOperation(state, 'setModel', async () => {
            await state.raw.setModel(model);
            await resizeState(state, 'model-changed');
          });
        },
        setView(view) {
          return enqueueStateOperation(state, 'setView', () => {
            if (typeof state.raw.setView !== 'function') {
              fail('capability_unavailable', 'Avatar renderer does not support setView', {
                operation: 'setView',
              });
            }
            return state.raw.setView(view);
          });
        },
        setSpeaking(active) {
          return enqueueStateOperation(state, 'setSpeaking', () => {
            if (typeof state.raw.setSpeaking !== 'function') {
              fail('capability_unavailable', 'Avatar renderer does not support setSpeaking', {
                operation: 'setSpeaking',
              });
            }
            return state.raw.setSpeaking(active);
          });
        },
        setSpeechPlayback(frame) {
          return enqueueStateOperation(state, 'setSpeechPlayback', () => {
            if (typeof state.raw.setSpeechPlayback !== 'function') return false;
            return state.raw.setSpeechPlayback(frame);
          });
        },
        focus(point) {
          return enqueueStateOperation(state, 'focus', () => state.raw.focus(point));
        },
        setEmotion(name) {
          return enqueueStateOperation(state, 'setEmotion', () => state.raw.setEmotion(name));
        },
        pause() {
          return enqueueStateOperation(state, 'pause', () => state.raw.pause());
        },
        resume() {
          return enqueueStateOperation(state, 'resume', () => state.raw.resume());
        },
        getState() {
          ensureState(state, 'getState');
          const rawState = state.raw.getState();
          return Object.freeze({
            ...(rawState && typeof rawState === 'object' ? rawState : {}),
            viewport: state.viewport,
          });
        },
        dispose() { disposeState(state); },
      });
    }

    async function mount(config) {
      if (disposed) fail('disposed', 'Avatar host has been disposed', { operation: 'mount' });
      const slot = String(config?.slot || '');
      const descriptor = slots.get(slot);
      if (!descriptor) fail('slot_unavailable', `Avatar slot "${slot}" is not registered`);
      if (active.has(slot) || pending.has(slot)) {
        fail('busy', `Avatar slot "${slot}" is already mounted or mounting`);
      }
      if (active.size + pending.size >= rendererLimit) {
        fail('busy', 'Avatar host renderer limit reached', { limit: rendererLimit });
      }
      const viewport = measureViewport(config, descriptor);
      const abortController = typeof AbortControllerImpl === 'function' ? new AbortControllerImpl() : null;
      let resolveDisposal = null;
      const disposal = new Promise((resolve) => { resolveDisposal = resolve; });
      const pendingState = { abortController, disposal, resolveDisposal };
      pending.set(slot, pendingState);
      let raw = null;
      let state = null;
      const restoreClip = clipViewport(descriptor, viewport);
      try {
        const controllerCreation = Promise.resolve().then(() => descriptor.createController({
          config,
          viewport,
          signal: abortController?.signal,
          fitLive2DModel,
        }));
        try {
          raw = await racePendingMount(controllerCreation, pendingState, slot);
        } catch (error) {
          // A renderer factory is third-party code and may ignore AbortSignal.
          // If disposal wins the race, observe the late settlement and release
          // any controller it eventually returns without keeping mount pending.
          controllerCreation.then(
            (lateRaw) => disposeRaw(lateRaw, `${slot}.late-create`),
            () => undefined,
          );
          throw error;
        }
        raw = ensureController(raw, slot);
        if (disposed || abortController?.signal?.aborted) {
          fail('disposed', 'Avatar host was disposed while mounting', { slot });
        }
        let resolveOperationDisposal = null;
        const operationDisposal = new Promise((resolve) => {
          resolveOperationDisposal = resolve;
        });
        state = {
          config,
          descriptor,
          restoreClip,
          raw,
          viewport,
          disposed: false,
          resizeObserver: null,
          resizeFrameId: null,
          resizeInFlight: false,
          queuedResizeReason: '',
          operationTail: Promise.resolve(),
          operationDisposal,
          resolveOperationDisposal,
          pendingOperations: 0,
        };
        await racePendingMount(raw.setModel(config.model), pendingState, slot);
        await racePendingMount(resizeState(state, 'mounted'), pendingState, slot);
        if (disposed || abortController?.signal?.aborted) {
          fail('disposed', 'Avatar host was disposed while mounting', { slot });
        }
        active.set(slot, state);
        attachResizeLifecycle(state);
        return publicController(state);
      } catch (error) {
        if (state) disposeState(state);
        else { disposeRaw(raw, `${slot}.mount-failed`); restoreClip(); }
        throw error;
      } finally {
        pending.delete(slot);
      }
    }

    return Object.freeze({
      get activeCount() { return active.size; },
      get pendingCount() { return pending.size; },
      mount,
      dispose() {
        if (disposed) return;
        disposed = true;
        for (const pendingState of pending.values()) {
          pendingState.abortController?.abort?.();
          pendingState.resolveDisposal?.();
          pendingState.resolveDisposal = null;
        }
        for (const state of Array.from(active.values())) disposeState(state);
        hostWindowStates.clear();
        syncHostWindowListener();
      },
    });
  }

  global.NekoMiniGameAvatarHost = Object.freeze({
    create,
    fitRectangle,
    fitPerspectiveModel,
    capturePerspectiveReference,
    preparePerspectiveReference,
    releasePerspectiveReference,
    fitLive2DModel,
    createSpeechAnalyser,
    Error: NekoMiniGameAvatarHostError,
  });
})(window);
