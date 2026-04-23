(function() {
  'use strict';

  if (!window.__NEKO_JUKEBOX_STANDALONE__) {
    return;
  }

  var IGNORE_DRAG_SELECTOR =
    'button, input, a, select, textarea, .jukebox-header-buttons, ' +
    '.jukebox-table tbody tr, .sam-panel, .jukebox-controls-row, .jukebox-resize-handle';
  var STANDALONE_INTERACTIVE_SELECTOR =
    '.jukebox-header, .jukebox-header-left, .jukebox-header-buttons, ' +
    '.jukebox-content, .jukebox-controls-row, .jukebox-calibration-section, .jukebox-notice';
  var MIN_WIDTH = 360;
  var MIN_HEIGHT = 300;

  function readWindowBounds() {
    return {
      x: typeof window.screenX === 'number' ? window.screenX : 0,
      y: typeof window.screenY === 'number' ? window.screenY : 0,
      width: typeof window.outerWidth === 'number' ? window.outerWidth : document.documentElement.clientWidth,
      height: typeof window.outerHeight === 'number' ? window.outerHeight : document.documentElement.clientHeight
    };
  }

  function readScreenWorkArea() {
    var screenObj = window.screen || {};
    return {
      x: typeof screenObj.availLeft === 'number' ? screenObj.availLeft : 0,
      y: typeof screenObj.availTop === 'number' ? screenObj.availTop : 0,
      width: typeof screenObj.availWidth === 'number' ? screenObj.availWidth : (screenObj.width || readWindowBounds().width),
      height: typeof screenObj.availHeight === 'number' ? screenObj.availHeight : (screenObj.height || readWindowBounds().height)
    };
  }

  function getEventScreenPoint(e) {
    var point = e.touches ? e.touches[0] : e;
    return {
      x: typeof point.screenX === 'number' ? point.screenX : point.clientX,
      y: typeof point.screenY === 'number' ? point.screenY : point.clientY
    };
  }

  function clampBounds(bounds, workArea, minWidth, minHeight) {
    var next = {
      x: Math.round(bounds.x),
      y: Math.round(bounds.y),
      width: Math.max(minWidth, Math.round(bounds.width)),
      height: Math.max(minHeight, Math.round(bounds.height))
    };

    if (!workArea) {
      return next;
    }

    next.width = Math.min(next.width, workArea.width);
    next.height = Math.min(next.height, workArea.height);

    var maxRight = workArea.x + workArea.width;
    var maxBottom = workArea.y + workArea.height;
    next.x = Math.max(workArea.x, Math.min(next.x, maxRight - next.width));
    next.y = Math.max(workArea.y, Math.min(next.y, maxBottom - next.height));

    return next;
  }

  function createWindowController() {
    var bridge = window.nekoJukeboxBridge || null;
    var hasBridgeBounds =
      bridge &&
      typeof bridge.getBounds === 'function' &&
      typeof bridge.setBounds === 'function';
    var canMoveWindow =
      typeof window.moveTo === 'function' &&
      typeof window.resizeTo === 'function';
    var canUseNativeDrag =
      bridge &&
      typeof bridge.dragStart === 'function' &&
      typeof bridge.dragStop === 'function';

    if (!hasBridgeBounds && !canMoveWindow) {
      return null;
    }

    var controller = {
      bridge: bridge,
      getBounds: function() {
        if (hasBridgeBounds) {
          return Promise.resolve(bridge.getBounds()).then(function(bounds) {
            return bounds || readWindowBounds();
          });
        }
        return Promise.resolve(readWindowBounds());
      },
      setBounds: function(bounds) {
        if (hasBridgeBounds) {
          bridge.setBounds(bounds.x, bounds.y, bounds.width, bounds.height);
          return;
        }

        // 双写一次位置，减少某些桌面环境 resize 后 top-left 漂移。
        window.moveTo(bounds.x, bounds.y);
        window.resizeTo(bounds.width, bounds.height);
        window.moveTo(bounds.x, bounds.y);
      },
      getWorkArea: function() {
        if (bridge && typeof bridge.getWorkArea === 'function') {
          return Promise.resolve(bridge.getWorkArea()).then(function(area) {
            return area || readScreenWorkArea();
          }).catch(function() {
            return readScreenWorkArea();
          });
        }
        return Promise.resolve(readScreenWorkArea());
      },
      nativeDrag: canUseNativeDrag ? {
        start: function(point) {
          bridge.dragStart(point.x, point.y);
        },
        stop: function() {
          bridge.dragStop();
        }
      } : null
    };

    return controller;
  }

  function disconnectLegacyStandaloneDrag() {
    if (!window.Jukebox || !window.Jukebox.State || !window.Jukebox.State._dragGuard) {
      return;
    }

    try {
      window.Jukebox.State._dragGuard.disconnect();
    } catch (_) {}
    window.Jukebox.State._dragGuard = null;
  }

  function neutralizeLegacyRegions(container) {
    disconnectLegacyStandaloneDrag();

    // 注意：这里不再把 .jukebox-container 及 header 强制设为 no-drag。
    // preload-jukebox.js 的 setupNativeDrag 已经铺好了正确的 CSS region：
    //   .jukebox-container → drag
    //   .jukebox-content / .jukebox-controls-row / .jukebox-header-buttons 等 → no-drag
    // 让 OS 原生拖拽接管 header 区域（和 /jukebox/manager 一致的零 IPC 路径），
    // 同时保留下面的 JS fallback：用户从 content 空白处起拖时仍走 bridge.dragStart。
    // 历史上这里把全部 region 改 no-drag 是为了让 JS 完全接管，现在我们有 native IPC drag
    // + 完整的 setupNativeDrag CSS 双保险，不需要再粗暴覆盖。

    // 仅清理旧 DOM 元素 .jukebox-drag-overlay（若存在）—— 这是历史遗留的透明覆盖层，
    // 可能挡住指针事件或造成 z-index 错位。
    var overlay = container.querySelector('.jukebox-drag-overlay');
    if (overlay) {
      overlay.style.webkitAppRegion = 'no-drag';
      overlay.style.pointerEvents = 'none';
      overlay.setAttribute('aria-hidden', 'true');
    }
  }

  function createRafScheduler(flush) {
    var rafId = 0;
    return {
      queue: function() {
        if (rafId) return;
        rafId = requestAnimationFrame(function() {
          rafId = 0;
          flush();
        });
      },
      cancel: function() {
        if (!rafId) return;
        cancelAnimationFrame(rafId);
        rafId = 0;
      }
    };
  }

  function queueDragBounds(state, point, scheduler) {
    if (!state) return;

    state.latestPoint = point;
    if (!state.startBounds) {
      return;
    }

    state.pendingBounds = clampBounds({
      x: state.startBounds.x + (point.x - state.startPointerX),
      y: state.startBounds.y + (point.y - state.startPointerY),
      width: state.startBounds.width,
      height: state.startBounds.height
    }, state.workArea, state.startBounds.width, state.startBounds.height);
    scheduler.queue();
  }

  function queueResizeBounds(state, point, scheduler) {
    if (!state) return;

    state.latestPoint = point;
    if (!state.startBounds) {
      return;
    }

    var dx = point.x - state.startPointerX;
    var dy = point.y - state.startPointerY;
    var nextBounds = {
      x: state.startBounds.x,
      y: state.startBounds.y,
      width: state.startBounds.width,
      height: state.startBounds.height
    };

    if (state.dir.indexOf('e') !== -1) {
      nextBounds.width += dx;
    }
    if (state.dir.indexOf('s') !== -1) {
      nextBounds.height += dy;
    }
    if (state.dir.indexOf('w') !== -1) {
      nextBounds.width -= dx;
      nextBounds.x += dx;
    }
    if (state.dir.indexOf('n') !== -1) {
      nextBounds.height -= dy;
      nextBounds.y += dy;
    }

    if (nextBounds.width < MIN_WIDTH) {
      if (state.dir.indexOf('w') !== -1) {
        nextBounds.x -= (MIN_WIDTH - nextBounds.width);
      }
      nextBounds.width = MIN_WIDTH;
    }
    if (nextBounds.height < MIN_HEIGHT) {
      if (state.dir.indexOf('n') !== -1) {
        nextBounds.y -= (MIN_HEIGHT - nextBounds.height);
      }
      nextBounds.height = MIN_HEIGHT;
    }

    var pending = clampBounds(nextBounds, state.workArea, MIN_WIDTH, MIN_HEIGHT);
    var right = nextBounds.x + nextBounds.width;
    var bottom = nextBounds.y + nextBounds.height;

    if (state.dir.indexOf('w') !== -1) {
      pending.width = Math.max(MIN_WIDTH, right - pending.x);
    }
    if (state.dir.indexOf('n') !== -1) {
      pending.height = Math.max(MIN_HEIGHT, bottom - pending.y);
    }

    state.pendingBounds = pending;
    scheduler.queue();
  }

  function bindStandaloneDrag(container, controller) {
    var dragRoot = container;
    if (!dragRoot) return;

    var state = null;
    var scheduler = createRafScheduler(function() {
      if (!state || !state.pendingBounds) return;
      var pending = state.pendingBounds;
      state.pendingBounds = null;
      controller.setBounds(pending);
    });

    function finishDrag() {
      if (!state) return;

      if (state.mode === 'pending') {
        state.released = true;
        return;
      }

      scheduler.cancel();

      if (state.mode === 'native' && controller.nativeDrag) {
        try { controller.nativeDrag.stop(); } catch (_) {}
      } else if (state.pendingBounds) {
        controller.setBounds(state.pendingBounds);
      }

      state = null;
      document.body.classList.remove('jukebox-dragging');
      if (window.Jukebox && window.Jukebox.State) {
        window.Jukebox.State.isDragging = false;
      }
    }

    async function onPointerDown(e) {
      if (typeof e.button === 'number' && e.button !== 0) {
        return;
      }

      if (e.target.closest(IGNORE_DRAG_SELECTOR)) {
        return;
      }

      e.preventDefault();
      e.stopPropagation();

      document.body.classList.add('jukebox-dragging');
      if (window.Jukebox && window.Jukebox.State) {
        window.Jukebox.State.isDragging = true;
      }

      var point = getEventScreenPoint(e);

      if (controller.nativeDrag) {
        state = { mode: 'native' };
        try {
          controller.nativeDrag.start(point);
          return;
        } catch (_) {
          state = null;
        }
      }

      var token = {};
      state = {
        mode: 'pending',
        token: token,
        startPointerX: point.x,
        startPointerY: point.y,
        startBounds: null,
        workArea: null,
        latestPoint: null,
        released: false,
        pendingBounds: null
      };

      var startBounds = await controller.getBounds();
      var workArea = await controller.getWorkArea();
      if (!state || state.token !== token) {
        return;
      }

      state.mode = 'manual';
      state.startBounds = startBounds;
      state.workArea = workArea;

      if (state.latestPoint) {
        queueDragBounds(state, state.latestPoint, scheduler);
      }
      if (state.released) {
        finishDrag();
      }
    }

    function onPointerMove(e) {
      if (!state || state.mode === 'native') return;

      e.preventDefault();
      queueDragBounds(state, getEventScreenPoint(e), scheduler);
    }

    dragRoot.addEventListener('mousedown', onPointerDown);
    dragRoot.addEventListener('touchstart', onPointerDown, { passive: false });
    document.addEventListener('mousemove', onPointerMove);
    document.addEventListener('touchmove', onPointerMove, { passive: false });
    document.addEventListener('mouseup', finishDrag);
    document.addEventListener('touchend', finishDrag);
    document.addEventListener('touchcancel', finishDrag);
    window.addEventListener('blur', finishDrag);

    if (window.Jukebox && window.Jukebox.State) {
      window.Jukebox.State._dragCleanup = function() {
        dragRoot.removeEventListener('mousedown', onPointerDown);
        dragRoot.removeEventListener('touchstart', onPointerDown);
        document.removeEventListener('mousemove', onPointerMove);
        document.removeEventListener('touchmove', onPointerMove);
        document.removeEventListener('mouseup', finishDrag);
        document.removeEventListener('touchend', finishDrag);
        document.removeEventListener('touchcancel', finishDrag);
        window.removeEventListener('blur', finishDrag);
        finishDrag();
        window.Jukebox.State._dragCleanup = null;
      };
    }
  }

  function bindStandaloneResize(container, controller) {
    var handles = container.querySelectorAll('.jukebox-resize-handle');
    if (!handles.length) return;

    var resizeState = null;
    var scheduler = createRafScheduler(function() {
      if (!resizeState || !resizeState.pendingBounds) return;
      var pending = resizeState.pendingBounds;
      resizeState.pendingBounds = null;
      controller.setBounds(pending);
    });

    function removeTransientListeners() {
      document.removeEventListener('mousemove', onPointerMove);
      document.removeEventListener('touchmove', onPointerMove);
      document.removeEventListener('mouseup', finishResize);
      document.removeEventListener('touchend', finishResize);
      document.removeEventListener('touchcancel', finishResize);
      window.removeEventListener('blur', finishResize);
    }

    function finishResize() {
      if (!resizeState) return;

      if (!resizeState.startBounds) {
        resizeState.released = true;
        removeTransientListeners();
        document.body.classList.remove('jukebox-resizing');
        return;
      }

      scheduler.cancel();
      removeTransientListeners();
      document.body.classList.remove('jukebox-resizing');

      if (resizeState.pendingBounds) {
        controller.setBounds(resizeState.pendingBounds);
      }

      resizeState = null;
      if (window.Jukebox && window.Jukebox.State) {
        window.Jukebox.State._resizeCleanup = null;
      }
    }

    function onPointerMove(e) {
      if (!resizeState) return;

      e.preventDefault();
      queueResizeBounds(resizeState, getEventScreenPoint(e), scheduler);
    }

    async function onPointerDown(e) {
      if (typeof e.button === 'number' && e.button !== 0) {
        return;
      }

      var dir = e.currentTarget && e.currentTarget.dataset ? e.currentTarget.dataset.dir : '';
      if (!dir) return;

      e.preventDefault();
      e.stopPropagation();

      if (window.Jukebox && window.Jukebox.State && window.Jukebox.State._resizeCleanup) {
        window.Jukebox.State._resizeCleanup();
      }

      document.body.classList.add('jukebox-resizing');
      var point = getEventScreenPoint(e);
      var token = {};
      resizeState = {
        token: token,
        dir: dir,
        startPointerX: point.x,
        startPointerY: point.y,
        startBounds: null,
        workArea: null,
        latestPoint: null,
        released: false,
        pendingBounds: null
      };

      document.addEventListener('mousemove', onPointerMove);
      document.addEventListener('touchmove', onPointerMove, { passive: false });
      document.addEventListener('mouseup', finishResize);
      document.addEventListener('touchend', finishResize);
      document.addEventListener('touchcancel', finishResize);
      window.addEventListener('blur', finishResize);

      if (window.Jukebox && window.Jukebox.State) {
        window.Jukebox.State._resizeCleanup = function() {
          finishResize();
        };
      }

      var startBounds = await controller.getBounds();
      var workArea = await controller.getWorkArea();
      if (!resizeState || resizeState.token !== token) {
        return;
      }

      resizeState.startBounds = startBounds;
      resizeState.workArea = workArea;

      if (resizeState.latestPoint) {
        queueResizeBounds(resizeState, resizeState.latestPoint, scheduler);
      }
      if (resizeState.released) {
        finishResize();
      }
    }

    handles.forEach(function(handle) {
      handle.addEventListener('mousedown', onPointerDown);
      handle.addEventListener('touchstart', onPointerDown, { passive: false });
    });
  }

  function mount() {
    if (mount._mounted) return true;
    if (!window.Jukebox || !window.Jukebox.State || !window.Jukebox.State.container) {
      return false;
    }

    var wrapper = window.Jukebox.State.container;
    var container = wrapper.querySelector('.jukebox-container');
    if (!container) {
      return false;
    }

    var controller = createWindowController();
    if (!controller) {
      return false;
    }

    neutralizeLegacyRegions(container);
    document.body.classList.add('neko-jukebox-standalone-page');

    bindStandaloneDrag(container, controller);
    bindStandaloneResize(container, controller);

    mount._mounted = true;
    return true;
  }

  window.NekoJukeboxStandalonePage = {
    mount: mount
  };
})();
