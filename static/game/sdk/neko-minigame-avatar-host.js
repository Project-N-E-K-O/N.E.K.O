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
  const disposedRawControllers = new WeakSet();

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
    const padding = Math.max(0, Number(fit.padding || 0));
    const multiplier = Math.max(0.05, Number(fit.scaleMultiplier || 1));
    const availableWidth = Math.max(1, width - padding * 2);
    const availableHeight = Math.max(1, height - padding * 2);
    const mode = String(fit.mode || 'contain');
    let nextScaleX;
    let nextScaleY;
    if (mode === 'native') {
      let baseline = live2dNativeBaselines.get(model);
      if (!baseline) {
        baseline = Object.freeze({ x: Number(model.scale.x), y: Number(model.scale.y) });
        live2dNativeBaselines.set(model, baseline);
      }
      nextScaleX = baseline.x * multiplier;
      nextScaleY = baseline.y * multiplier;
    } else {
      const ratio = mode === 'cover'
        ? Math.max(availableWidth / modelWidth, availableHeight / modelHeight)
        : Math.min(availableWidth / modelWidth, availableHeight / modelHeight);
      nextScaleX = Number(model.scale.x) * ratio * multiplier;
      nextScaleY = Number(model.scale.y) * ratio * multiplier;
    }
    model.scale.set(nextScaleX, nextScaleY);

    const [vertical, horizontal] = normalizeAlignment(fit.align);
    const fittedWidth = Number(model.width);
    const fittedHeight = Number(model.height);
    const boundsX = horizontal === 'left'
      ? padding
      : (horizontal === 'right' ? width - padding - fittedWidth : (width - fittedWidth) / 2);
    const boundsY = vertical === 'top'
      ? padding
      : (vertical === 'bottom' ? height - padding - fittedHeight : (height - fittedHeight) / 2);
    const anchorX = Number(model.anchor?.x || 0);
    const anchorY = Number(model.anchor?.y || 0);
    model.x = boundsX + fittedWidth * anchorX;
    model.y = boundsY + fittedHeight * anchorY;
    return Object.freeze({
      width: fittedWidth,
      height: fittedHeight,
      x: model.x,
      y: model.y,
      scaleX: Number(model.scale.x),
      scaleY: Number(model.scale.y),
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
    }

    function ensureState(state, operation) {
      if (disposed) fail('disposed', 'Avatar host has been disposed', { operation });
      if (!state || state.disposed) fail('disposed', 'Avatar controller has been disposed', { operation });
    }

    function publicController(state) {
      return Object.freeze({
        get disposed() { return disposed || state.disposed; },
        async setModel(model) {
          return enqueueStateOperation(state, 'setModel', async () => {
            await state.raw.setModel(model);
            await resizeState(state, 'model-changed');
          });
        },
        focus(point) {
          ensureState(state, 'focus');
          return state.raw.focus(point);
        },
        setEmotion(name) {
          ensureState(state, 'setEmotion');
          return state.raw.setEmotion(name);
        },
        pause() {
          ensureState(state, 'pause');
          return state.raw.pause();
        },
        resume() {
          ensureState(state, 'resume');
          return state.raw.resume();
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
        else disposeRaw(raw, `${slot}.mount-failed`);
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
    fitLive2DModel,
    Error: NekoMiniGameAvatarHostError,
  });
})(window);
