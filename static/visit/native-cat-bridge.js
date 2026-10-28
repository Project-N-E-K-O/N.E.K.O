/**
 * Read-only bridge from the existing single-cat runtime to cat-form visits.
 *
 * The bridge never starts an action, owns a timer or listens to Cat Mind.  It
 * only turns the currently visible local cat into a small semantic snapshot
 * that is safe to relay: official resource identity plus normalized position.
 */
(function installNativeCatVisitBridge(global) {
  'use strict';

  var TIERS = Object.freeze({ cat1: true, cat2: true, cat3: true });
  var OFFICIAL_SLOTS = Object.freeze([
    'idle.cat1', 'idle.cat2', 'idle.cat3',
    'click.cat1', 'click.cat2', 'click.cat3',
    'drag.cat1', 'drag.cat2', 'drag.cat3', 'drag.rapid',
    'movement.cat1.walking', 'movement.cat1.stretch', 'movement.cat1.interactive',
    'action.cat1.eat', 'action.cat1.play_yarn', 'playground.cat1.air'
  ]);
  var OFFICIAL_SLOT_SET = OFFICIAL_SLOTS.reduce(function (result, slot) {
    result[slot] = true;
    return result;
  }, Object.create(null));

  function finite(value) {
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function q16(value, extent) {
    if (!(extent > 0)) return null;
    return Math.round(clamp(value / extent, 0, 1) * 65535);
  }

  function visibleCat() {
    if (!global.document || typeof global.document.querySelectorAll !== 'function') return null;
    var buttons = global.document.querySelectorAll('.neko-idle-return-btn');
    for (var index = 0; index < buttons.length; index += 1) {
      var button = buttons[index];
      var tier = button && button.getAttribute && button.getAttribute('data-neko-idle-tier');
      var container = button && button.closest && button.closest('[id$="-return-button-container"]');
      if (!TIERS[tier] || !container || !container.style || container.style.display === 'none') continue;
      var rect = elementRect(container);
      if (!rect || !(rect.width > 0) || !(rect.height > 0)) continue;
      return { button: button, container: container, tier: tier, rect: rect };
    }
    return null;
  }

  function elementRect(element) {
    try {
      if (typeof global._getNekoDesktopVirtualElementRect === 'function') {
        var virtualRect = global._getNekoDesktopVirtualElementRect(element);
        if (validRect(virtualRect)) return normalizeRect(virtualRect);
      }
      if (element && typeof element.getBoundingClientRect === 'function') {
        var localRect = element.getBoundingClientRect();
        if (validRect(localRect)) return normalizeRect(localRect);
      }
    } catch (_) { /* a later sample can retry */ }
    return null;
  }

  function validRect(rect) {
    if (!rect || typeof rect !== 'object') return false;
    var left = finite(rect.left == null ? rect.x : rect.left);
    var top = finite(rect.top == null ? rect.y : rect.top);
    var width = finite(rect.width);
    var height = finite(rect.height);
    return left !== null && top !== null && width > 0 && height > 0;
  }

  function normalizeRect(rect) {
    var left = finite(rect.left == null ? rect.x : rect.left);
    var top = finite(rect.top == null ? rect.y : rect.top);
    var width = finite(rect.width);
    var height = finite(rect.height);
    return { left: left, top: top, width: width, height: height };
  }

  function viewportSize() {
    try {
      if (typeof global._getNekoDesktopVirtualViewportSize === 'function') {
        var virtualSize = global._getNekoDesktopVirtualViewportSize();
        var virtualWidth = finite(virtualSize && virtualSize.width);
        var virtualHeight = finite(virtualSize && virtualSize.height);
        if (virtualWidth > 0 && virtualHeight > 0) {
          return { width: virtualWidth, height: virtualHeight };
        }
      }
    } catch (_) { /* use the page viewport */ }
    return {
      width: Math.max(1, finite(global.innerWidth) || 1),
      height: Math.max(1, finite(global.innerHeight) || 1)
    };
  }

  function classOn(element, name) {
    return !!(element && element.classList && element.classList.contains(name));
  }

  function apiActive(apiName, button) {
    var api = global[apiName];
    if (!api || typeof api.isActive !== 'function') return false;
    try { return api.isActive(button) === true; } catch (_) { return false; }
  }

  function playgroundCatBody(state) {
    if (!state || !state.bodies) return null;
    try {
      if (typeof state.bodies.get === 'function') return state.bodies.get('cat') || null;
    } catch (_) { /* malformed local state is treated as unavailable */ }
    return null;
  }

  function modeFor(button, container) {
    var playground = button && button.__nekoIdleCat1PlaygroundDropState;
    if (playground && playground.active === true && playground.released !== true) {
      var playgroundBody = playgroundCatBody(playground);
      if (playground.draggingBodyId === 'cat'
          || container.getAttribute('data-neko-cat1-playground-dragging') === 'cat'
          || (playgroundBody && playgroundBody.dragging === true)) return 'drag';
      if ((playgroundBody && playgroundBody.grounded === false)
          || playground.phase === 'ballistic' || playground.phase === 'dropping') return 'airborne';
    }

    var dragging = container.getAttribute('data-dragging');
    if (dragging === 'true' || dragging === 'pending'
        || classOn(button, 'is-drag-action') || classOn(container, 'is-drag-action')) return 'drag';

    if (apiActive('NekoDesktopWindowGravity', button)
        || classOn(button, 'is-desktop-window-gravity')
        || classOn(container, 'is-desktop-window-gravity')) {
      var gravity = global.NekoDesktopWindowGravity;
      var gravityState = null;
      try { gravityState = gravity && typeof gravity.getState === 'function' ? gravity.getState() : null; } catch (_) {}
      if (!gravityState || gravityState.phase !== 'resting') return 'airborne';
    }

    var edgeController = global.NekoEdgePeekController;
    var edgeActive = false;
    try {
      edgeActive = !!(edgeController && typeof edgeController.getActiveMode === 'function'
        && edgeController.getActiveMode(button));
    } catch (_) { edgeActive = false; }
    if (edgeActive
        || apiActive('NekoDesktopWindowTopEdgePerch', button)
        || apiActive('NekoDesktopWindowEdgePeek', button)
        || classOn(container, 'is-cat1-desktop-window-top-edge-active')
        || classOn(container, 'is-cat1-desktop-window-edge-peeking')) return 'edge';

    var target = container.getAttribute('data-neko-cat1-target')
      || button.getAttribute('data-neko-cat1-target');
    if (target || classOn(container, 'is-cat1-compact-mirror-active')
        || classOn(button, 'is-cat1-on-compact-top-edge')) return 'follow';

    var substate = container.getAttribute('data-neko-cat1-substate')
      || button.getAttribute('data-neko-cat1-substate');
    if (substate === 'walking' || classOn(button, 'is-cat1-walking')
        || classOn(container, 'is-cat1-walking')
        || classOn(button, 'is-cat1-desktop-window-edge-peek-walking')
        || classOn(button, 'is-cat1-desktop-window-top-edge-walking')) return 'walk';
    return 'idle';
  }

  function facingFor(button, container) {
    var value = container.getAttribute('data-neko-cat1-facing')
      || button.getAttribute('data-neko-cat1-facing');
    if (value === 'right' || value === 'left') return value;
    if (classOn(button, 'is-cat1-facing-right')
        || classOn(container, 'is-cat1-facing-right')
        || classOn(button, 'is-cat1-desktop-window-edge-peek-facing-right')
        || classOn(button, 'is-cat1-desktop-window-top-edge-facing-right')) return 'right';
    return 'left';
  }

  function resourceUrl(button) {
    if (!button || typeof button.querySelector !== 'function') return '';
    var art = button.querySelector('.neko-idle-return-art');
    if (!art) return '';
    return String(art.__nekoIdleGifPlaybackBaseSrc
      || art.currentSrc
      || (art.getAttribute && art.getAttribute('src'))
      || art.src
      || '');
  }

  function normalizePath(value) {
    if (typeof value !== 'string' || !value) return '';
    try {
      return new URL(value, global.location && global.location.href).pathname;
    } catch (_) {
      return value.split(/[?#]/, 1)[0];
    }
  }

  function matchingSlots(url) {
    var path = normalizePath(url);
    var registry = global.NekoCatResourceRegistry;
    if (!path || !registry || typeof registry.getAppearanceForGroup !== 'function') return [];
    var group = registry.selectedAppearanceGroupId;
    return OFFICIAL_SLOTS.filter(function (slot) {
      var result = null;
      try { result = registry.getAppearanceForGroup(group, slot, { random: false }); } catch (_) { return false; }
      var urls = result && Array.isArray(result.urls) ? result.urls : [];
      return urls.some(function (candidate) { return normalizePath(candidate) === path; });
    });
  }

  function fallbackSlot(tier, mode) {
    if (mode === 'airborne' && tier === 'cat1') return 'playground.cat1.air';
    if (mode === 'drag') return 'drag.' + tier;
    if ((mode === 'walk' || mode === 'follow') && tier === 'cat1') return 'movement.cat1.walking';
    return 'idle.' + tier;
  }

  function selectSlot(tier, mode, url) {
    var matches = matchingSlots(url);
    if (!matches.length) return fallbackSlot(tier, mode);
    var expectedTierSuffix = '.' + tier;
    var priorities = [];
    if (mode === 'airborne') priorities.push('playground.' + tier + '.air');
    if (mode === 'drag') priorities.push('drag.' + tier, 'drag.rapid');
    if (mode === 'walk' || mode === 'follow') priorities.push('movement.' + tier + '.walking');
    priorities.push('idle.' + tier, 'click.' + tier, 'drag.' + tier);
    for (var index = 0; index < priorities.length; index += 1) {
      if (matches.indexOf(priorities[index]) !== -1) return priorities[index];
    }
    var sameTier = matches.find(function (slot) {
      return slot.indexOf(expectedTierSuffix) !== -1 || slot.indexOf(expectedTierSuffix + '.') !== -1;
    });
    return sameTier || matches[0];
  }

  function snapshot() {
    var active = visibleCat();
    if (!active) return null;
    var size = viewportSize();
    var centerX = active.rect.left + active.rect.width / 2;
    var centerY = active.rect.top + active.rect.height / 2;
    var xQ16 = q16(centerX, size.width);
    var yQ16 = q16(centerY, size.height);
    if (xQ16 === null || yQ16 === null) return null;
    var mode = modeFor(active.button, active.container);
    var slot = selectSlot(active.tier, mode, resourceUrl(active.button));
    if (!OFFICIAL_SLOT_SET[slot]) slot = fallbackSlot(active.tier, mode);
    return Object.freeze({
      tier: active.tier,
      slot: slot,
      mode: mode,
      facing: facingFor(active.button, active.container),
      x_q16: xQ16,
      y_q16: yQ16
    });
  }

  global.NekoIdleCatVisitBridge = Object.freeze({ snapshot: snapshot });
})(typeof window !== 'undefined' ? window : globalThis);
