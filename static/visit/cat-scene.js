/**
 * Visible cat-form visit scene.
 *
 * The local owner cat remains the existing N.E.K.O single-cat instance. This
 * module adds only visit identity, a read-only peer mirror, pose transport and
 * the guarded return-to-model flow. It never owns Cat Mind or invents motion.
 */
(function installVisitCatScene(global) {
  'use strict';

  var CAT_SCENE_VERSION = 2;
  var VALID_VISIT_ID = /^[A-Za-z0-9_-]{22}$/;
  var VALID_ASSETS = { dev_neko: true, yui_neko: true };
  var VALID_TIERS = { cat1: true, cat2: true, cat3: true };
  var VALID_MODES = { idle: true, walk: true, drag: true, airborne: true, edge: true, follow: true };
  var VALID_SLOTS = {
    'idle.cat1': true, 'idle.cat2': true, 'idle.cat3': true,
    'click.cat1': true, 'click.cat2': true, 'click.cat3': true,
    'drag.cat1': true, 'drag.cat2': true, 'drag.cat3': true, 'drag.rapid': true,
    'movement.cat1.walking': true, 'movement.cat1.stretch': true,
    'movement.cat1.interactive': true, 'action.cat1.eat': true,
    'action.cat1.play_yarn': true, 'playground.cat1.air': true
  };
  var ACTIONS = {
    greet: { slot: 'click.cat1', duration: 2500 },
    play: { slot: 'action.cat1.play_yarn', duration: 4000 },
    rest: { slot: 'idle.cat2', duration: 3500 },
    move: { slot: 'movement.cat1.walking', duration: 1800 }
  };
  var SAMPLE_INTERVAL_MS = 100;
  var COMMIT_SETTLE_MS = 360;

  function emptySlot(actor) {
    return {
      actor: actor,
      asset: 'dev_neko',
      image: null,
      card: null,
      label: null,
      timer: 0,
      collisionTimer: 0,
      collisionElement: null,
      token: 0,
      lastActionSeq: 0,
      motionId: 0,
      sampleSeq: 0,
      committedMotionId: 0,
      resourceSlot: '',
      hasPose: false
    };
  }

  var scene = {
    active: false,
    visitId: '',
    side: '',
    phase: '',
    returning: false,
    root: null,
    layer: null,
    toolbar: null,
    status: null,
    returnButton: null,
    appearanceOverride: '',
    nativeSelf: {
      container: null,
      button: null,
      label: null,
      frame: 0,
      entryRequested: false,
      lastGeometry: '',
      collisionTimer: 0,
      collisionElement: null
    },
    pose: {
      generation: 0,
      lastSampleAt: 0,
      lastSignature: '',
      lastChangeAt: 0,
      motionId: 0,
      sampleSeq: 0,
      committed: true,
      queue: Promise.resolve()
    },
    geometry: { frame: 0, until: 0 },
    slots: { self: emptySlot('self'), peer: emptySlot('peer') }
  };

  function normalizeAsset(value) {
    return VALID_ASSETS[value] ? value : 'dev_neko';
  }

  function t(key, fallback) {
    if (typeof global.t !== 'function') return fallback;
    var translated = global.t(key);
    return translated && translated !== key ? translated : fallback;
  }

  function setStatus(text) {
    if (scene.status) scene.status.textContent = text || '';
  }

  function resolveAppearance(asset, slot, seed) {
    var registry = global.NekoCatResourceRegistry;
    if (!registry || !VALID_SLOTS[slot]) return null;
    if (typeof registry.getAppearanceForGroup === 'function') {
      return registry.getAppearanceForGroup(asset, slot, { seed: seed });
    }
    if (asset === registry.selectedAppearanceGroupId && typeof registry.getAppearance === 'function') {
      return registry.getAppearance(slot, { seed: seed });
    }
    return null;
  }

  function artUrl(asset, slot, seed, token) {
    var result = resolveAppearance(asset, slot, seed);
    if (!result || result.available !== true || typeof result.url !== 'string' || !result.url) return '';
    return result.url.replace(/#.*$/, '') + '#visit-' + String(token >>> 0);
  }

  function clearSlotTimer(slot) {
    if (slot.timer) clearTimeout(slot.timer);
    slot.timer = 0;
  }

  function clearCollisionPulse(target) {
    if (!target) return;
    if (target.collisionTimer) clearTimeout(target.collisionTimer);
    target.collisionTimer = 0;
    if (target.collisionElement && target.collisionElement.classList) {
      target.collisionElement.classList.remove('neko-visit-cat-collision');
    }
    target.collisionElement = null;
  }

  function pulseCollisionTarget(target, element) {
    if (!target || !element || !element.classList) return;
    clearCollisionPulse(target);
    target.collisionElement = element;
    element.classList.add('neko-visit-cat-collision');
    target.collisionTimer = setTimeout(function () {
      clearCollisionPulse(target);
    }, 180);
  }

  function setSlotArt(slot, resourceSlot, seed) {
    if (!slot.image || !VALID_SLOTS[resourceSlot]) return false;
    if (slot.resourceSlot === resourceSlot && slot.image.getAttribute('src')) return true;
    slot.token = (slot.token + 1) >>> 0;
    var url = artUrl(slot.asset, resourceSlot, seed >>> 0, slot.token);
    if (!url) return false;
    slot.resourceSlot = resourceSlot;
    slot.image.src = url;
    slot.image.setAttribute('src', url);
    slot.image.setAttribute('data-visit-cat-resource-slot', resourceSlot);
    if (slot.card) slot.card.setAttribute('data-visit-cat-action-state', resourceSlot);
    return true;
  }

  function setIdle(slot, tier) {
    var normalizedTier = VALID_TIERS[tier] ? tier : 'cat1';
    setSlotArt(slot, 'idle.' + normalizedTier, 0);
  }

  function updateControls() {
    if (scene.returnButton) {
      scene.returnButton.disabled = !scene.active || scene.phase === 'ending' || scene.returning;
    }
  }

  function makeElement(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function makeCatCard(actor, title) {
    var card = makeElement('section', 'neko-visit-cat-card');
    card.hidden = true;
    card.setAttribute('data-visit-cat-actor', actor);
    card.setAttribute('data-neko-visit-cat-input', 'actor');
    var stage = makeElement('div', 'neko-visit-cat-art-stage');
    var image = makeElement('img', 'neko-visit-cat-art');
    image.setAttribute('data-visit-cat-art', '');
    image.alt = title;
    image.draggable = false;
    stage.appendChild(image);
    card.appendChild(stage);
    var label = makeElement('div', 'neko-visit-cat-card-heading');
    label.hidden = true;
    label.setAttribute('data-visit-cat-owner-label', actor);
    label.appendChild(makeElement('strong', 'neko-visit-cat-owner', title));
    scene.slots[actor].image = image;
    scene.slots[actor].card = card;
    scene.slots[actor].label = label;
    return { card: card, label: label };
  }

  function ensureScene() {
    if (scene.root) return scene.root;
    var root = makeElement('section', 'neko-visit-cat-scene');
    root.id = 'neko-visit-cat-scene';
    root.hidden = true;
    root.setAttribute('aria-label', t('visit.catScene.title', '猫猫串门'));
    root.setAttribute('data-visit-cat-scene', '');
    root.setAttribute('data-neko-visit-cat-world', '');

    var header = makeElement('header', 'neko-visit-cat-header');
    var titleGroup = makeElement('div', 'neko-visit-cat-title-group');
    titleGroup.appendChild(makeElement('strong', 'neko-visit-cat-title', t('visit.catScene.title', '猫猫串门')));
    scene.status = makeElement('span', 'neko-visit-cat-status', t('visit.catScene.active', '正在串门'));
    titleGroup.appendChild(scene.status);
    scene.returnButton = makeElement('button', 'neko-visit-cat-return', t('visit.catScene.returnModel', '返回模型'));
    scene.returnButton.type = 'button';
    scene.returnButton.setAttribute('data-visit-cat-return', '');
    scene.returnButton.addEventListener('click', function () { requestModelReturn(); });
    header.appendChild(titleGroup);
    header.appendChild(scene.returnButton);

    var cats = makeElement('div', 'neko-visit-cat-world-layer');
    var selfCard = makeCatCard('self', t('visit.catScene.myCat', '我的猫'));
    var peerCard = makeCatCard('peer', t('visit.catScene.peerCat', '对方的猫'));
    cats.appendChild(selfCard.card);
    cats.appendChild(selfCard.label);
    cats.appendChild(peerCard.card);
    cats.appendChild(peerCard.label);

    var toolbar = makeElement('div', 'neko-visit-cat-toolbar');
    toolbar.setAttribute('data-neko-visit-cat-input', 'dock');
    toolbar.appendChild(header);
    root.appendChild(cats);
    root.appendChild(toolbar);
    (document.body || document.documentElement).appendChild(root);
    scene.root = root;
    scene.layer = cats;
    scene.toolbar = toolbar;
    return root;
  }

  function emitGeometryChanged() {
    try {
      global.dispatchEvent(new CustomEvent('neko:visit-cat-geometry-changed'));
    } catch (_) { /* plain browser fallback has no desktop crop listener */ }
  }

  function geometryNow() {
    return typeof performance !== 'undefined' && typeof performance.now === 'function'
      ? performance.now()
      : Date.now();
  }

  function runGeometryPulse(timestamp) {
    scene.geometry.frame = 0;
    syncFloatingLabels();
    emitGeometryChanged();
    if (!scene.active || Number(timestamp) >= scene.geometry.until
        || typeof global.requestAnimationFrame !== 'function') return;
    scene.geometry.frame = global.requestAnimationFrame(runGeometryPulse);
  }

  function pulseGeometry(duration) {
    var now = geometryNow();
    scene.geometry.until = Math.max(scene.geometry.until, now + Math.max(0, Number(duration) || 0));
    emitGeometryChanged();
    if (!scene.geometry.frame && typeof global.requestAnimationFrame === 'function') {
      scene.geometry.frame = global.requestAnimationFrame(runGeometryPulse);
    }
  }

  function stopGeometryPulse() {
    if (scene.geometry.frame && typeof global.cancelAnimationFrame === 'function') {
      global.cancelAnimationFrame(scene.geometry.frame);
    }
    scene.geometry.frame = 0;
    scene.geometry.until = 0;
    emitGeometryChanged();
  }

  function findNativeSelfCat() {
    if (typeof document.querySelectorAll !== 'function') return null;
    var buttons = document.querySelectorAll('.neko-idle-return-btn');
    for (var index = 0; index < buttons.length; index += 1) {
      var button = buttons[index];
      var tier = button.getAttribute && button.getAttribute('data-neko-idle-tier');
      var container = button.closest && button.closest('[id$="-return-button-container"]');
      if (!VALID_TIERS[tier] || !container || !container.style || container.style.display === 'none') continue;
      return { button: button, container: container };
    }
    return null;
  }

  function currentIdleAppearance() {
    try {
      if (typeof global.getNekoGoodbyeIdleAppearance === 'function') {
        return global.getNekoGoodbyeIdleAppearance() === 'ball' ? 'ball' : 'cat';
      }
    } catch (_) { /* use the page snapshot */ }
    return global.__nekoGoodbyeIdleAppearance === 'ball' ? 'ball' : 'cat';
  }

  function setIdleAppearance(mode, reason) {
    global.__nekoGoodbyeIdleAppearance = mode;
    try {
      global.dispatchEvent(new CustomEvent('neko:goodbye-idle-appearance', {
        detail: { mode: mode, reason: reason, timestamp: Date.now() }
      }));
    } catch (_) { /* the visit still uses its fallback card */ }
  }

  function forceCatAppearance() {
    if (scene.appearanceOverride) return;
    var previous = currentIdleAppearance();
    if (previous !== 'ball') return;
    scene.appearanceOverride = previous;
    setIdleAppearance('cat', 'cat-visit-entry');
  }

  function restoreIdleAppearance() {
    var previous = scene.appearanceOverride;
    scene.appearanceOverride = '';
    if (previous) setIdleAppearance(previous, 'cat-visit-exit');
  }

  function dropNativeSelfBinding() {
    var nativeSelf = scene.nativeSelf;
    clearCollisionPulse(nativeSelf);
    if (nativeSelf.container) {
      nativeSelf.container.removeAttribute('data-neko-visit-cat-input');
      nativeSelf.container.removeAttribute('data-visit-cat-actor');
    }
    nativeSelf.container = null;
    nativeSelf.button = null;
    nativeSelf.lastGeometry = '';
    if (nativeSelf.label) nativeSelf.label.hidden = true;
    if (scene.slots.self.card) scene.slots.self.card.hidden = !scene.slots.self.hasPose;
    if (scene.slots.self.label) scene.slots.self.label.hidden = !scene.slots.self.hasPose;
  }

  function clearNativeSelfBinding() {
    if (scene.nativeSelf.frame && typeof global.cancelAnimationFrame === 'function') {
      global.cancelAnimationFrame(scene.nativeSelf.frame);
    }
    scene.nativeSelf.frame = 0;
    dropNativeSelfBinding();
  }

  function bindNativeSelf() {
    if (!scene.active || scene.nativeSelf.container) return false;
    var found = findNativeSelfCat();
    if (!found || !scene.layer) return false;
    var nativeSelf = scene.nativeSelf;
    nativeSelf.container = found.container;
    nativeSelf.button = found.button;
    nativeSelf.container.setAttribute('data-neko-visit-cat-input', 'actor');
    nativeSelf.container.setAttribute('data-visit-cat-actor', 'self');
    if (!nativeSelf.label) {
      nativeSelf.label = makeElement('div', 'neko-visit-cat-native-label', t('visit.catScene.myCat', '我的猫'));
      nativeSelf.label.setAttribute('data-visit-cat-owner-label', 'self');
      scene.layer.appendChild(nativeSelf.label);
    }
    nativeSelf.label.hidden = false;
    if (scene.slots.self.card) scene.slots.self.card.hidden = true;
    if (scene.slots.self.label) scene.slots.self.label.hidden = true;
    emitGeometryChanged();
    return true;
  }

  function validSnapshot(snapshot) {
    return !!(snapshot && VALID_TIERS[snapshot.tier] && VALID_SLOTS[snapshot.slot]
      && VALID_MODES[snapshot.mode] && (snapshot.facing === 'left' || snapshot.facing === 'right')
      && Number.isSafeInteger(snapshot.x_q16) && snapshot.x_q16 >= 0 && snapshot.x_q16 <= 65535
      && Number.isSafeInteger(snapshot.y_q16) && snapshot.y_q16 >= 0 && snapshot.y_q16 <= 65535);
  }

  function poseSignature(snapshot) {
    return [snapshot.tier, snapshot.slot, snapshot.mode, snapshot.facing,
      snapshot.x_q16, snapshot.y_q16].join('|');
  }

  function nextMotionId(current) {
    return current >= 0xffffffff ? 1 : Math.max(1, (current >>> 0) + 1);
  }

  function resetPosePump() {
    scene.pose.generation += 1;
    scene.pose.lastSampleAt = 0;
    scene.pose.lastSignature = '';
    scene.pose.lastChangeAt = 0;
    scene.pose.motionId = 0;
    scene.pose.sampleSeq = 0;
    scene.pose.committed = true;
    scene.pose.queue = Promise.resolve();
  }

  function queuePose(phase, snapshot) {
    var generation = scene.pose.generation;
    var visitId = scene.visitId;
    var payload = {
      motion_id: scene.pose.motionId,
      sample_seq: scene.pose.sampleSeq,
      x_q16: snapshot.x_q16,
      y_q16: snapshot.y_q16,
      mode: snapshot.mode,
      facing: snapshot.facing,
      tier: snapshot.tier,
      slot: snapshot.slot
    };
    scene.pose.queue = scene.pose.queue.catch(function () { return false; }).then(function () {
      if (!scene.active || scene.visitId !== visitId || scene.pose.generation !== generation) return false;
      var chat = global.nekoVisitChat;
      if (!chat || typeof chat.requestCatPose !== 'function') return false;
      return chat.requestCatPose(visitId, phase, payload);
    }).then(function (result) {
      if (!scene.active || scene.visitId !== visitId || scene.pose.generation !== generation) return false;
      var cursor = result && Number(result.current_motion_id);
      if (result && result.stale && Number.isSafeInteger(cursor)) {
        scene.pose.motionId = Math.max(scene.pose.motionId, cursor);
        scene.pose.sampleSeq = 0;
        scene.pose.committed = true;
        scene.pose.lastSignature = '';
      } else if (!result && phase === 'sample') {
        scene.pose.lastSignature = '';
      } else if (!result && phase === 'commit') {
        scene.pose.committed = false;
        scene.pose.lastChangeAt = geometryNow();
      }
      return result;
    });
  }

  function pumpNativePose(timestamp) {
    if (!scene.active || scene.phase === 'ending') return;
    var now = Number(timestamp);
    if (!Number.isFinite(now)) now = geometryNow();
    if (scene.pose.lastSampleAt && now - scene.pose.lastSampleAt < SAMPLE_INTERVAL_MS) return;
    scene.pose.lastSampleAt = now;
    var bridge = global.NekoIdleCatVisitBridge;
    var snapshot = null;
    try { snapshot = bridge && typeof bridge.snapshot === 'function' ? bridge.snapshot() : null; } catch (_) {}
    if (!validSnapshot(snapshot)) return;
    var signature = poseSignature(snapshot);
    if (signature !== scene.pose.lastSignature) {
      if (!scene.pose.motionId || scene.pose.committed) {
        scene.pose.motionId = nextMotionId(scene.pose.motionId);
        scene.pose.sampleSeq = 0;
      }
      scene.pose.committed = false;
      scene.pose.sampleSeq += 1;
      scene.pose.lastSignature = signature;
      scene.pose.lastChangeAt = now;
      queuePose('sample', snapshot);
      return;
    }
    if (!scene.pose.committed && now - scene.pose.lastChangeAt >= COMMIT_SETTLE_MS) {
      scene.pose.sampleSeq += 1;
      scene.pose.committed = true;
      queuePose('commit', snapshot);
    }
  }

  function syncNativeSelf(timestamp) {
    scene.nativeSelf.frame = 0;
    if (!scene.active) return;
    var nativeSelf = scene.nativeSelf;
    if (nativeSelf.container && (!nativeSelf.container.isConnected
        || nativeSelf.container.style.display === 'none')) {
      dropNativeSelfBinding();
    }
    if (!nativeSelf.container) bindNativeSelf();
    if (nativeSelf.container && nativeSelf.label
        && typeof nativeSelf.container.getBoundingClientRect === 'function') {
      var rect = nativeSelf.container.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) {
        var labelRect = nativeSelf.label.getBoundingClientRect
          ? nativeSelf.label.getBoundingClientRect() : null;
        var labelHeight = labelRect && labelRect.height > 0 ? labelRect.height : 26;
        nativeSelf.label.style.left = Math.round(rect.left + rect.width / 2) + 'px';
        nativeSelf.label.style.top = Math.max(Math.round(rect.top), Math.ceil(labelHeight + 4)) + 'px';
        var geometry = [Math.round(rect.left), Math.round(rect.top),
          Math.round(rect.width), Math.round(rect.height)].join(':');
        if (geometry !== nativeSelf.lastGeometry) {
          nativeSelf.lastGeometry = geometry;
          emitGeometryChanged();
        }
      }
    }
    pumpNativePose(timestamp);
    if (typeof global.requestAnimationFrame === 'function') {
      scene.nativeSelf.frame = global.requestAnimationFrame(syncNativeSelf);
    }
  }

  function requestNativeCatEntry() {
    if (scene.nativeSelf.entryRequested || scene.nativeSelf.container) return;
    scene.nativeSelf.entryRequested = true;
    try {
      global.dispatchEvent(new CustomEvent('live2d-goodbye-click', {
        detail: { source: 'cat-visit-entry', reason: 'cat-visit-entry' }
      }));
    } catch (_) { /* a fallback self card remains available */ }
  }

  function resetSlot(slot, asset) {
    clearSlotTimer(slot);
    clearCollisionPulse(slot);
    slot.asset = normalizeAsset(asset);
    slot.lastActionSeq = 0;
    slot.motionId = 0;
    slot.sampleSeq = 0;
    slot.committedMotionId = 0;
    slot.resourceSlot = '';
    slot.hasPose = false;
    slot.token = 0;
    if (slot.card) {
      slot.card.hidden = true;
      slot.card.setAttribute('data-visit-cat-asset', slot.asset);
    }
    if (slot.label) slot.label.hidden = true;
    setIdle(slot, 'cat1');
  }

  function activate(frame) {
    var root = ensureScene();
    var sameVisit = scene.active && scene.visitId === frame.visit_id;
    scene.active = true;
    scene.visitId = frame.visit_id;
    scene.side = frame.side === 'host' || frame.side === 'guest' ? frame.side : '';
    scene.phase = 'active';
    scene.returning = false;
    root.hidden = false;
    root.setAttribute('data-visit-id', scene.visitId);
    root.setAttribute('data-visit-side', scene.side);
    document.body.classList.add('neko-visit-cat-scene-active');
    if (!sameVisit) {
      resetPosePump();
      resetSlot(scene.slots.self, frame.self_cat_asset);
      resetSlot(scene.slots.peer, frame.peer_cat_asset);
      scene.nativeSelf.entryRequested = false;
    }
    forceCatAppearance();
    bindNativeSelf();
    if (!scene.nativeSelf.container) requestNativeCatEntry();
    if (!scene.nativeSelf.frame && typeof global.requestAnimationFrame === 'function') {
      scene.nativeSelf.frame = global.requestAnimationFrame(syncNativeSelf);
    }
    setStatus(t('visit.catScene.active', '正在串门'));
    updateControls();
    emitGeometryChanged();
  }

  function deactivate() {
    var endedVisitId = scene.visitId;
    clearSlotTimer(scene.slots.self);
    clearSlotTimer(scene.slots.peer);
    clearCollisionPulse(scene.slots.self);
    clearCollisionPulse(scene.slots.peer);
    clearCollisionPulse(scene.nativeSelf);
    scene.active = false;
    scene.visitId = '';
    scene.side = '';
    scene.phase = '';
    scene.returning = false;
    clearNativeSelfBinding();
    restoreIdleAppearance();
    resetPosePump();
    stopGeometryPulse();
    if (scene.root) scene.root.hidden = true;
    if (document.body && document.body.classList) document.body.classList.remove('neko-visit-cat-scene-active');
    updateControls();
    if (VALID_VISIT_ID.test(String(endedVisitId || ''))) {
      try {
        global.dispatchEvent(new CustomEvent('neko:visit-cat-scene-deactivated', {
          detail: { visit_id: endedVisitId }
        }));
      } catch (_) {}
    }
  }

  function onDisplayState(event) {
    var frame = event && event.detail ? event.detail : event;
    if (!frame || frame.type !== 'visit_state_change' || !VALID_VISIT_ID.test(String(frame.visit_id || ''))) return;
    if (frame.cat_scene === CAT_SCENE_VERSION
        && ['started', 'departed', 'wrap_up', 'ending', 'reconnecting'].indexOf(frame.action) !== -1
        && (!scene.active || frame.visit_id !== scene.visitId)) {
      activate(frame);
      if (frame.action === 'started') return;
    }
    if (!scene.active || frame.visit_id !== scene.visitId) return;
    if (frame.action === 'ended') {
      deactivate();
      return;
    }
    if (frame.action === 'ending') {
      scene.phase = 'ending';
      setStatus(t('visit.catScene.ending', '正在退出串门…'));
      updateControls();
    } else if (frame.action === 'reconnecting') {
      setStatus(t('visit.catScene.reconnecting', '正在重新连接…'));
    } else if (frame.action === 'started' || frame.action === 'departed' || frame.action === 'wrap_up') {
      scene.phase = frame.action === 'wrap_up' ? 'wrap_up' : 'active';
      setStatus(frame.action === 'wrap_up'
        ? t('visit.catScene.wrapUp', '准备回家')
        : t('visit.catScene.active', '正在串门'));
      updateControls();
    }
  }

  function applyPose(slot, frame) {
    var x = frame.x_q16 / 65535 * 100;
    var y = frame.y_q16 / 65535 * 100;
    var duration = frame.phase === 'sample' ? (frame.mode === 'drag' ? 90 : 140) : 180;
    slot.hasPose = true;
    slot.motionId = frame.motion_id;
    slot.sampleSeq = frame.sample_seq;
    if (frame.phase === 'commit') slot.committedMotionId = frame.motion_id;
    clearSlotTimer(slot);
    if (slot.card && slot.card.style && typeof slot.card.style.setProperty === 'function') {
      slot.card.style.setProperty('--neko-visit-cat-move-duration', duration + 'ms');
      slot.card.style.setProperty('--neko-visit-cat-x', x + '%');
      slot.card.style.setProperty('--neko-visit-cat-y', y + '%');
      slot.card.setAttribute('data-visit-cat-facing', frame.facing);
      slot.card.setAttribute('data-visit-cat-mode', frame.mode);
      slot.card.setAttribute('data-visit-cat-tier', frame.tier);
      slot.card.hidden = frame.actor === 'self' && !!scene.nativeSelf.container;
    }
    if (slot.label) slot.label.hidden = frame.actor === 'self' && !!scene.nativeSelf.container;
    setSlotArt(slot, frame.slot, frame.motion_id ^ frame.sample_seq);
    syncSlotLabel(slot);
    pulseGeometry(duration + 60);
  }

  function syncSlotLabel(slot) {
    if (!slot || !slot.card || !slot.label || slot.card.hidden || slot.label.hidden
        || typeof slot.card.getBoundingClientRect !== 'function') return;
    var rect = slot.card.getBoundingClientRect();
    if (!rect || !(rect.width > 0) || !(rect.height > 0)) return;
    var labelRect = slot.label.getBoundingClientRect
      ? slot.label.getBoundingClientRect() : null;
    var labelHeight = labelRect && labelRect.height > 0 ? labelRect.height : 26;
    slot.label.style.left = Math.round(rect.left + rect.width / 2) + 'px';
    slot.label.style.top = Math.max(0, Math.round(rect.top - labelHeight - 4)) + 'px';
  }

  function syncFloatingLabels() {
    syncSlotLabel(scene.slots.self);
    syncSlotLabel(scene.slots.peer);
  }

  function onCatPose(event) {
    var frame = event && event.detail ? event.detail : event;
    if (!scene.active || !frame || frame.type !== 'visit_cat_pose' || frame.visit_id !== scene.visitId) return;
    var slot = scene.slots[frame.actor];
    if (!slot || (frame.phase !== 'sample' && frame.phase !== 'commit')
        || !Number.isSafeInteger(frame.motion_id) || frame.motion_id < 1 || frame.motion_id > 0xffffffff
        || !Number.isSafeInteger(frame.sample_seq) || frame.sample_seq < 1 || frame.sample_seq > 0xffffffff
        || !Number.isSafeInteger(frame.x_q16) || frame.x_q16 < 0 || frame.x_q16 > 65535
        || !Number.isSafeInteger(frame.y_q16) || frame.y_q16 < 0 || frame.y_q16 > 65535
        || !VALID_MODES[frame.mode] || !VALID_TIERS[frame.tier] || !VALID_SLOTS[frame.slot]
        || (frame.facing !== 'left' && frame.facing !== 'right')) return;
    if (frame.phase === 'sample' && frame.motion_id <= slot.committedMotionId) return;
    if (frame.motion_id < slot.motionId
        || (frame.motion_id === slot.motionId && frame.sample_seq <= slot.sampleSeq)) return;
    if (frame.actor === 'self' && frame.motion_id > scene.pose.motionId) {
      scene.pose.motionId = frame.motion_id;
      scene.pose.sampleSeq = frame.sample_seq;
      scene.pose.committed = frame.phase === 'commit';
    }
    applyPose(slot, frame);
  }

  function onCatAction(event) {
    var frame = event && event.detail ? event.detail : event;
    if (!scene.active || !frame || frame.type !== 'visit_cat_scene' || frame.visit_id !== scene.visitId) return;
    var slot = scene.slots[frame.actor];
    var action = ACTIONS[frame.kind];
    if (!slot || !action || !Number.isSafeInteger(frame.seq) || frame.seq <= slot.lastActionSeq
        || !Number.isSafeInteger(frame.seed) || frame.seed < 0 || frame.seed > 0xffffffff) return;
    slot.lastActionSeq = frame.seq;
    if (frame.actor === 'self' && scene.nativeSelf.container) return;
    clearSlotTimer(slot);
    if (!setSlotArt(slot, action.slot, frame.seed)) return;
    if (slot.card) slot.card.hidden = false;
    if (slot.label) slot.label.hidden = false;
    syncSlotLabel(slot);
    var token = slot.token;
    slot.timer = setTimeout(function () {
      if (!scene.active || slot.token !== token) return;
      slot.timer = 0;
      var tier = slot.card && slot.card.getAttribute('data-visit-cat-tier');
      setIdle(slot, VALID_TIERS[tier] ? tier : 'cat1');
    }, action.duration);
  }

  function onWorldCollision(event) {
    var detail = event && event.detail ? event.detail : event;
    if (!scene.active || !detail || detail.visit_id !== scene.visitId) return;
    var kind = typeof detail.kind === 'string' ? detail.kind : '';
    if (!kind || kind === 'cats' || kind === 'yarn:self') {
      pulseCollisionTarget(
        scene.nativeSelf.container ? scene.nativeSelf : scene.slots.self,
        scene.nativeSelf.container || scene.slots.self.card
      );
    }
    if (!kind || kind === 'cats' || kind === 'yarn:peer') {
      pulseCollisionTarget(scene.slots.peer, scene.slots.peer.card);
    }
  }

  function confirmReturn() {
    var message = t('visit.catScene.returnConfirm', '返回模型会退出本次串门，确定要离开吗？');
    var title = t('visit.catScene.returnTitle', '退出猫猫串门');
    if (typeof global.showConfirm === 'function') {
      return Promise.resolve(global.showConfirm(message, title, {
        danger: true,
        confirmText: t('visit.catScene.returnConfirmAction', '退出并返回模型')
      }));
    }
    if (typeof global.confirm === 'function') return Promise.resolve(global.confirm(message));
    return Promise.resolve(false);
  }

  function guardModelReturn(commitReturn) {
    if (!scene.active) return false;
    if (scene.returning) return true;
    var visitId = scene.visitId;
    scene.returning = true;
    updateControls();
    confirmReturn().then(function (confirmed) {
      if (!confirmed || !scene.active || scene.visitId !== visitId) {
        scene.returning = false;
        updateControls();
        return false;
      }
      var chat = global.nekoVisitChat;
      if (!chat || typeof chat.endCatVisitForModelReturn !== 'function') return false;
      setStatus(t('visit.catScene.ending', '正在退出串门…'));
      return Promise.resolve(chat.endCatVisitForModelReturn(visitId)).then(function (ok) {
        if (!ok || !scene.active || scene.visitId !== visitId) return false;
        deactivate();
        if (typeof commitReturn === 'function') commitReturn();
        return true;
      });
    }).catch(function () {
      return false;
    }).then(function (completed) {
      if (!completed && scene.active && scene.visitId === visitId) {
        scene.returning = false;
        setStatus(t('visit.catScene.active', '正在串门'));
        updateControls();
      }
    });
    return true;
  }

  function findReturnButton() {
    if (typeof document.querySelectorAll !== 'function') return null;
    var buttons = document.querySelectorAll('.neko-idle-return-btn');
    for (var index = 0; index < buttons.length; index += 1) {
      var tier = buttons[index].getAttribute && buttons[index].getAttribute('data-neko-idle-tier');
      if (tier && tier !== 'none') return buttons[index];
    }
    return null;
  }

  function requestModelReturn() {
    var button = scene.nativeSelf.button || findReturnButton();
    return guardModelReturn(function () {
      if (button && typeof button.click === 'function') button.click();
    });
  }

  global.addEventListener('neko-visit-display-state', onDisplayState);
  global.addEventListener('neko-visit-cat-scene', onCatAction);
  global.addEventListener('neko-visit-cat-pose', onCatPose);
  global.addEventListener('neko:visit-cat-world-collision', onWorldCollision);
  global.addEventListener('pagehide', function () {
    global.removeEventListener('neko-visit-display-state', onDisplayState);
    global.removeEventListener('neko-visit-cat-scene', onCatAction);
    global.removeEventListener('neko-visit-cat-pose', onCatPose);
    global.removeEventListener('neko:visit-cat-world-collision', onWorldCollision);
    deactivate();
  }, { once: true });

  global.nekoVisitCatScene = Object.freeze({
    guardModelReturn: guardModelReturn,
    requestModelReturn: requestModelReturn,
    isActive: function () { return scene.active; }
  });

  if (typeof global.nekoVisitDisplayStateSnapshot === 'function') {
    var snapshot = global.nekoVisitDisplayStateSnapshot();
    if (snapshot) onDisplayState(snapshot);
  }
  if (typeof global.nekoVisitCatPoseSnapshots === 'function') {
    var poseSnapshots = global.nekoVisitCatPoseSnapshots();
    if (Array.isArray(poseSnapshots)) poseSnapshots.forEach(onCatPose);
  }
})(typeof window !== 'undefined' ? window : globalThis);
