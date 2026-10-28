/**
 * Host-authoritative shared world for cat-form visits.
 *
 * Owner cat motion remains in cat-scene.js.  This module owns only shared
 * props and shared collision results: every participant submits normalized
 * intent, while the host automatically simulates and publishes one world.
 */
(function installVisitCatWorld(global) {
  'use strict';

  var VISIT_ID_RE = /^[A-Za-z0-9_-]{22}$/;
  var Q16_MAX = 65535;
  var Q12_MAX = 4095;
  var Q12_MIN = -4096;
  var U32_MAX = 0xffffffff;
  var TICK_MS = 67;
  var REPLAY_WAIT_MS = 180;
  var COMMIT_SETTLE_MS = 360;
  var POINTER_SEND_MS = 60;
  var INTENT_KINDS = {
    yarn_spawn: true,
    yarn_grab: true,
    yarn_release: true,
    question_tap: true,
    pointer: true
  };

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, Number(value) || 0));
  }

  function clampInt(value, low, high) {
    return Math.round(clamp(value, low, high));
  }

  function nextU32(value) {
    return value >= U32_MAX ? 1 : Math.max(1, (value >>> 0) + 1);
  }

  function frozenCopy(value) {
    return Object.freeze(Object.assign({}, value));
  }

  function defaultWorld() {
    return {
      world_seq: 0,
      yarn_active: false,
      yarn_x_q16: 32768,
      yarn_y_q16: 19661,
      yarn_vx_q12: 0,
      yarn_vy_q12: 0,
      yarn_holder: 'none',
      // 普通猫形态串门不凭空增加问号块。字段继续保留以兼容已发布的
      // 共享世界帧；只有后续复用现有 Playground 入口时才显式激活。
      question_active: false,
      question_x_q16: 32768,
      question_y_q16: 13107,
      collision_seq: 0
    };
  }

  function readSettings() {
    var raw = global.nekoVisitCatWorldSettings;
    raw = raw && typeof raw === 'object' ? raw : {};
    return Object.freeze({
      gravityQ12PerSecond: clampInt(raw.gravity_q12_per_second || 2800, 0, 4095),
      bounce: clamp(raw.bounce || 0.72, 0.1, 0.95),
      damping: clamp(raw.damping || 0.992, 0.85, 1),
      yarnRadiusQ16: clampInt(raw.yarn_radius_q16 || 2600, 1200, 6000),
      catRadiusQ16: clampInt(raw.cat_radius_q16 || 5000, 2400, 9000),
      floorInsetQ16: clampInt(raw.floor_inset_q16 || 2400, 1000, 6000)
    });
  }

  var state = {
    active: false,
    ending: false,
    visitId: '',
    side: '',
    layer: null,
    yarn: null,
    question: null,
    world: defaultWorld(),
    settings: readSettings(),
    poses: { self: null, peer: null },
    lastAppliedWorldSeq: 0,
    publishedWorldSeq: 0,
    simulationStarted: false,
    yarnGrounded: false,
    tickTimer: 0,
    startTimer: 0,
    settleTimer: 0,
    collisionPulseTimer: 0,
    lastTickAt: 0,
    drag: null,
    dragAuthority: null,
    intentSequence: 0,
    sentIntentCursor: 0,
    intentQueue: Promise.resolve(),
    intentToken: 0,
    pendingIntents: [],
    pointerLastSentAt: 0,
    outbound: [],
    outboundBusy: false,
    outboundToken: 0,
    catOverlap: false,
    yarnContacts: { self: false, peer: false }
  };

  function makeElement(tag, className) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    return element;
  }

  function geometryChanged() {
    try { global.dispatchEvent(new CustomEvent('neko:visit-cat-geometry-changed')); } catch (_) {}
  }

  function findSceneRoot() {
    if (typeof document.getElementById === 'function') {
      var byId = document.getElementById('neko-visit-cat-scene');
      if (byId) return byId;
    }
    return typeof document.querySelector === 'function'
      ? document.querySelector('[data-neko-visit-cat-world]')
      : null;
  }

  function pointFromEvent(event) {
    var width = Math.max(1, Number(global.innerWidth) || 1);
    var height = Math.max(1, Number(global.innerHeight) || 1);
    var clientX = Number(event && event.clientX);
    var clientY = Number(event && event.clientY);
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
    return {
      x_q16: clampInt(clientX / width * Q16_MAX, 0, Q16_MAX),
      y_q16: clampInt(clientY / height * Q16_MAX, 0, Q16_MAX)
    };
  }

  function nextIntentId() {
    state.intentSequence = nextU32(state.intentSequence);
    return state.intentSequence;
  }

  function callApi(method, payload) {
    var api = global.nekoVisitCatWorldApi;
    if (!api || typeof api[method] !== 'function') return Promise.resolve(false);
    try { return Promise.resolve(api[method](payload)).catch(function () { return false; }); }
    catch (_) { return Promise.resolve(false); }
  }

  function sendIntent(kind, point, intentId) {
    if (!state.active || state.ending || !INTENT_KINDS[kind] || kind === 'pointer') return false;
    var id = Number.isSafeInteger(intentId) ? intentId : nextIntentId();
    var visitId = state.visitId;
    var token = state.intentToken;
    var payload = {
      visit_id: state.visitId,
      intent_id: id,
      kind: kind,
      x_q16: point.x_q16,
      y_q16: point.y_q16
    };
    // Reliable intents share one per-visit queue. A quick down/up must never
    // let release overtake grab at the local HTTP boundary.
    state.intentQueue = state.intentQueue.catch(function () { return false; }).then(function () {
      if (!state.active || state.visitId !== visitId || state.intentToken !== token) return false;
      if (payload.intent_id <= state.sentIntentCursor) {
        var previousId = payload.intent_id;
        payload = Object.assign({}, payload, { intent_id: nextIntentId() });
        if (state.drag && state.drag.intentId === previousId) state.drag.intentId = payload.intent_id;
      }
      return callApi('sendIntent', payload);
    }).then(function (result) {
      if (result && result.ok === true) {
        state.sentIntentCursor = Math.max(state.sentIntentCursor, payload.intent_id);
      }
      var cursor = result && result.stale && Number(result.current_intent_id);
      if (!state.active || state.visitId !== visitId || state.intentToken !== token
          || !Number.isSafeInteger(cursor)) return result;
      state.intentSequence = Math.max(state.intentSequence, cursor);
      state.sentIntentCursor = Math.max(state.sentIntentCursor, cursor);
      var staleId = payload.intent_id;
      var retryId = nextIntentId();
      if (state.drag && state.drag.intentId === staleId) state.drag.intentId = retryId;
      payload = Object.assign({}, payload, { intent_id: retryId });
      return callApi('sendIntent', payload).then(function (retryResult) {
        if (retryResult && retryResult.ok === true) {
          state.sentIntentCursor = Math.max(state.sentIntentCursor, retryId);
        }
        return retryResult;
      });
    });
    return id;
  }

  function sendPointer(point, intentId) {
    if (!state.active || state.ending || !Number.isSafeInteger(intentId)) return false;
    callApi('sendPointer', {
      visit_id: state.visitId,
      intent_id: intentId,
      x_q16: point.x_q16,
      y_q16: point.y_q16
    });
    return true;
  }

  function onQuestionClick(event) {
    if (!state.world.question_active) return;
    var point = pointFromEvent(event) || {
      x_q16: state.world.question_x_q16,
      y_q16: state.world.question_y_q16
    };
    sendIntent('question_tap', point);
  }

  function onYarnPointerDown(event) {
    if (!state.world.yarn_active || (event.button !== undefined && event.button !== 0)) return;
    var point = pointFromEvent(event);
    if (!point) return;
    try { event.preventDefault(); event.stopPropagation(); } catch (_) {}
    var intentId = sendIntent('yarn_grab', point);
    if (!intentId) return;
    state.drag = { pointerId: event.pointerId, intentId: intentId };
    state.pointerLastSentAt = 0;
    if (state.yarn && typeof state.yarn.setPointerCapture === 'function'
        && event.pointerId !== undefined) {
      try { state.yarn.setPointerCapture(event.pointerId); } catch (_) {}
    }
  }

  function dragMatches(event) {
    if (!state.drag) return false;
    return state.drag.pointerId === undefined || state.drag.pointerId === null
      || event.pointerId === undefined || event.pointerId === state.drag.pointerId;
  }

  function onYarnPointerMove(event) {
    if (!dragMatches(event)) return;
    var point = pointFromEvent(event);
    if (!point) return;
    var timestamp = Number(event.timeStamp);
    if (!Number.isFinite(timestamp)) timestamp = Date.now();
    if (state.pointerLastSentAt && timestamp - state.pointerLastSentAt < POINTER_SEND_MS) return;
    state.pointerLastSentAt = timestamp;
    sendPointer(point, state.drag.intentId);
  }

  function endYarnDrag(event, cancelled) {
    if (!dragMatches(event)) return;
    var drag = state.drag;
    state.drag = null;
    state.pointerLastSentAt = 0;
    var point = pointFromEvent(event) || {
      x_q16: state.world.yarn_x_q16,
      y_q16: state.world.yarn_y_q16
    };
    // A cancelled native pointer sequence still releases host authority.  It
    // merely produces zero/short throw velocity instead of leaving a holder
    // that neither participant can grab again.
    if (cancelled) state.pointerLastSentAt = 0;
    // Reliable intents use a strictly increasing id. Pointer samples retain
    // the grab id; release is authorized by the current holder actor.
    sendIntent('yarn_release', point);
  }

  function ensureLayer() {
    if (state.layer && state.layer.isConnected !== false) return state.layer;
    var root = findSceneRoot();
    if (!root) return null;
    var layer = makeElement('div', 'neko-visit-cat-shared-world');
    layer.setAttribute('data-visit-cat-shared-world', '');

    var question = makeElement('button', 'neko-visit-cat-question');
    question.type = 'button';
    question.setAttribute('aria-label', '共享问号块');
    question.setAttribute('data-neko-visit-cat-input', 'object');
    question.setAttribute('data-visit-cat-shared-object', 'question');
    question.addEventListener('click', onQuestionClick);

    var yarn = makeElement('button', 'neko-visit-cat-yarn');
    yarn.type = 'button';
    yarn.setAttribute('aria-label', '共享毛线球');
    yarn.setAttribute('data-neko-visit-cat-input', 'object');
    yarn.setAttribute('data-visit-cat-shared-object', 'yarn');
    yarn.addEventListener('pointerdown', onYarnPointerDown);
    yarn.addEventListener('pointermove', onYarnPointerMove);
    yarn.addEventListener('pointerup', function (event) { endYarnDrag(event, false); });
    yarn.addEventListener('pointercancel', function (event) { endYarnDrag(event, true); });

    layer.appendChild(question);
    layer.appendChild(yarn);
    root.appendChild(layer);
    state.layer = layer;
    state.question = question;
    state.yarn = yarn;
    renderWorld();
    geometryChanged();
    return layer;
  }

  function setPosition(element, xQ16, yQ16) {
    if (!element || !element.style) return;
    element.style.left = (xQ16 / Q16_MAX * 100) + '%';
    element.style.top = (yQ16 / Q16_MAX * 100) + '%';
  }

  function renderWorld() {
    if (!state.active || !ensureLayer()) return;
    var world = state.world;
    state.yarn.hidden = !world.yarn_active;
    state.question.hidden = !world.question_active;
    setPosition(state.yarn, world.yarn_x_q16, world.yarn_y_q16);
    setPosition(state.question, world.question_x_q16, world.question_y_q16);
    state.yarn.setAttribute('data-visit-cat-holder', world.yarn_holder);
    geometryChanged();
  }

  function removeLayer() {
    if (state.layer && state.layer.parentNode) state.layer.parentNode.removeChild(state.layer);
    state.layer = null;
    state.yarn = null;
    state.question = null;
    geometryChanged();
  }

  function actorToHolder(actor) {
    if (actor === 'self') return 'host';
    if (actor === 'peer') return 'guest';
    return '';
  }

  function validQ16(value) {
    return Number.isSafeInteger(value) && value >= 0 && value <= Q16_MAX;
  }

  function validIntent(frame) {
    return !!(frame && frame.type === 'visit_cat_world_intent'
      && frame.visit_id === state.visitId && (frame.actor === 'self' || frame.actor === 'peer')
      && INTENT_KINDS[frame.kind] && Number.isSafeInteger(frame.intent_id)
      && frame.intent_id >= 1 && frame.intent_id <= U32_MAX
      && validQ16(frame.x_q16) && validQ16(frame.y_q16));
  }

  function rememberPointer(frame) {
    var drag = state.dragAuthority;
    if (!drag || drag.intentId !== frame.intent_id || drag.actor !== frame.actor) return false;
    var timestamp = Date.now();
    drag.samples.push({ x: frame.x_q16, y: frame.y_q16, at: timestamp });
    while (drag.samples.length > 4 || (drag.samples[0] && drag.samples[0].at < timestamp - 180)) {
      drag.samples.shift();
    }
    state.world.yarn_x_q16 = frame.x_q16;
    state.world.yarn_y_q16 = frame.y_q16;
    state.world.yarn_vx_q12 = 0;
    state.world.yarn_vy_q12 = 0;
    state.yarnGrounded = false;
    publish('frame');
    renderWorld();
    return true;
  }

  function releaseVelocity(drag, point) {
    var samples = drag && drag.samples ? drag.samples.slice() : [];
    samples.push({ x: point.x_q16, y: point.y_q16, at: Date.now() });
    if (samples.length < 2) return { x: 0, y: 0 };
    var first = samples[0];
    var last = samples[samples.length - 1];
    var seconds = Math.max(0.016, (last.at - first.at) / 1000);
    return {
      x: clampInt((last.x - first.x) / Q16_MAX / seconds * 4096, Q12_MIN, Q12_MAX),
      y: clampInt((last.y - first.y) / Q16_MAX / seconds * 4096, Q12_MIN, Q12_MAX)
    };
  }

  function applyHostIntent(frame) {
    if (!state.active || state.side !== 'host' || !validIntent(frame)) return false;
    if (!state.simulationStarted) {
      state.pendingIntents.push(Object.assign({}, frame));
      while (state.pendingIntents.length > 64) state.pendingIntents.shift();
      return true;
    }
    var world = state.world;
    var point = { x_q16: frame.x_q16, y_q16: frame.y_q16 };
    var holder = actorToHolder(frame.actor);
    if (frame.kind === 'pointer') return rememberPointer(frame);
    if (frame.kind === 'question_tap' || frame.kind === 'yarn_spawn') {
      world.yarn_active = true;
      world.yarn_x_q16 = point.x_q16;
      world.yarn_y_q16 = point.y_q16;
      world.yarn_vx_q12 = 0;
      world.yarn_vy_q12 = 0;
      world.yarn_holder = 'none';
      state.yarnGrounded = false;
      state.dragAuthority = null;
    } else if (frame.kind === 'yarn_grab') {
      if (!world.yarn_active || world.yarn_holder !== 'none') return false;
      world.yarn_holder = holder;
      world.yarn_x_q16 = point.x_q16;
      world.yarn_y_q16 = point.y_q16;
      world.yarn_vx_q12 = 0;
      world.yarn_vy_q12 = 0;
      state.yarnGrounded = false;
      state.dragAuthority = {
        actor: frame.actor,
        intentId: frame.intent_id,
        samples: [{ x: frame.x_q16, y: frame.y_q16, at: Date.now() }]
      };
    } else if (frame.kind === 'yarn_release') {
      var drag = state.dragAuthority;
      if (!drag || drag.actor !== frame.actor || world.yarn_holder !== holder) return false;
      var velocity = releaseVelocity(drag, point);
      world.yarn_holder = 'none';
      world.yarn_x_q16 = point.x_q16;
      world.yarn_y_q16 = point.y_q16;
      world.yarn_vx_q12 = velocity.x;
      world.yarn_vy_q12 = velocity.y;
      state.yarnGrounded = false;
      state.dragAuthority = null;
    }
    renderWorld();
    publish('commit');
    scheduleSettleCommit();
    return true;
  }

  function stateSnapshotForWire() {
    var world = state.world;
    return {
      yarn_active: world.yarn_active === true,
      yarn_x_q16: clampInt(world.yarn_x_q16, 0, Q16_MAX),
      yarn_y_q16: clampInt(world.yarn_y_q16, 0, Q16_MAX),
      yarn_vx_q12: clampInt(world.yarn_vx_q12, Q12_MIN, Q12_MAX),
      yarn_vy_q12: clampInt(world.yarn_vy_q12, Q12_MIN, Q12_MAX),
      yarn_holder: world.yarn_holder === 'host' || world.yarn_holder === 'guest'
        ? world.yarn_holder : 'none',
      question_active: world.question_active === true,
      question_x_q16: clampInt(world.question_x_q16, 0, Q16_MAX),
      question_y_q16: clampInt(world.question_y_q16, 0, Q16_MAX),
      collision_seq: clampInt(world.collision_seq, 0, U32_MAX)
    };
  }

  function drainOutbound() {
    if (state.outboundBusy || !state.outbound.length || !state.active || state.ending
        || state.side !== 'host') return;
    state.outboundBusy = true;
    var item = state.outbound.shift();
    var token = state.outboundToken;
    var visitId = state.visitId;
    state.publishedWorldSeq = nextU32(state.publishedWorldSeq);
    var payload = Object.assign({
      visit_id: state.visitId,
      phase: item.phase,
      world_seq: state.publishedWorldSeq
    }, item.snapshot);
    callApi('sendState', payload).then(function (result) {
      if (token !== state.outboundToken || !state.active || state.visitId !== visitId) return;
      var cursor = result && Number(result.current_world_seq);
      if (result && result.stale === true && Number.isSafeInteger(cursor)
          && cursor >= 1 && cursor <= U32_MAX) {
        state.publishedWorldSeq = Math.max(state.publishedWorldSeq, cursor);
        if (!item.retried) {
          item.retried = true;
          state.outbound.unshift(item);
        }
      }
      state.outboundBusy = false;
      drainOutbound();
    });
  }

  function publish(phase) {
    if (!state.active || state.ending || state.side !== 'host' || !state.simulationStarted) return false;
    var item = { phase: phase === 'commit' ? 'commit' : 'frame', snapshot: stateSnapshotForWire() };
    if (item.phase === 'frame' && state.outbound.length
        && state.outbound[state.outbound.length - 1].phase === 'frame') {
      state.outbound[state.outbound.length - 1] = item;
    } else {
      state.outbound.push(item);
    }
    drainOutbound();
    return true;
  }

  function scheduleSettleCommit() {
    if (state.ending) return;
    if (state.settleTimer) clearTimeout(state.settleTimer);
    state.settleTimer = setTimeout(function () {
      state.settleTimer = 0;
      publish('commit');
    }, COMMIT_SETTLE_MS);
  }

  function pulseCollision(kind) {
    if (!state.layer) return;
    state.layer.classList.add('is-collision');
    if (state.collisionPulseTimer) clearTimeout(state.collisionPulseTimer);
    state.collisionPulseTimer = setTimeout(function () {
      state.collisionPulseTimer = 0;
      if (state.layer) state.layer.classList.remove('is-collision');
    }, 180);
    try {
      global.dispatchEvent(new CustomEvent('neko:visit-cat-world-collision', {
        detail: {
          visit_id: state.visitId,
          collision_seq: state.world.collision_seq,
          kind: typeof kind === 'string' ? kind : ''
        }
      }));
    } catch (_) {}
  }

  function collideYarnWithPose(actor, pose) {
    if (!pose || !state.world.yarn_active || state.world.yarn_holder !== 'none') {
      state.yarnContacts[actor] = false;
      return false;
    }
    var dx = state.world.yarn_x_q16 - pose.x_q16;
    var dy = state.world.yarn_y_q16 - pose.y_q16;
    var distance = Math.hypot(dx, dy);
    var minimum = state.settings.yarnRadiusQ16 + state.settings.catRadiusQ16;
    if (!distance || distance >= minimum) {
      state.yarnContacts[actor] = false;
      return false;
    }
    var nx = dx / distance;
    var ny = dy / distance;
    state.world.yarn_x_q16 = clampInt(pose.x_q16 + nx * minimum, 0, Q16_MAX);
    state.world.yarn_y_q16 = clampInt(pose.y_q16 + ny * minimum, 0, Q16_MAX);
    var impulse = 1450;
    state.world.yarn_vx_q12 = clampInt(state.world.yarn_vx_q12 + nx * impulse, Q12_MIN, Q12_MAX);
    state.world.yarn_vy_q12 = clampInt(state.world.yarn_vy_q12 + ny * impulse, Q12_MIN, Q12_MAX);
    state.yarnGrounded = false;
    if (!state.yarnContacts[actor]) {
      state.world.collision_seq = state.world.collision_seq >= U32_MAX
        ? 0 : state.world.collision_seq + 1;
      pulseCollision('yarn:' + actor);
    }
    state.yarnContacts[actor] = true;
    return true;
  }

  function detectCatCollision() {
    var first = state.poses.self;
    var second = state.poses.peer;
    if (!first || !second) {
      state.catOverlap = false;
      return false;
    }
    var distance = Math.hypot(first.x_q16 - second.x_q16, first.y_q16 - second.y_q16);
    var overlapping = distance <= state.settings.catRadiusQ16 * 2;
    if (!overlapping) {
      state.catOverlap = false;
      return false;
    }
    if (state.catOverlap) return false;
    state.catOverlap = true;
    state.world.collision_seq = state.world.collision_seq >= U32_MAX
      ? 0 : state.world.collision_seq + 1;
    pulseCollision('cats');
    return true;
  }

  function simulate(timestamp) {
    if (!state.active || state.ending || !state.simulationStarted || state.side !== 'host') return;
    var now = Number(timestamp);
    if (!Number.isFinite(now)) now = Date.now();
    var previous = state.lastTickAt || now - TICK_MS;
    var dt = clamp((now - previous) / 1000, 0.008, 0.12);
    state.lastTickAt = now;
    var world = state.world;
    var changed = false;
    if (world.yarn_active && world.yarn_holder === 'none') {
      var wasGrounded = state.yarnGrounded;
      if (!state.yarnGrounded) {
        world.yarn_vy_q12 = clampInt(
          world.yarn_vy_q12 + state.settings.gravityQ12PerSecond * dt,
          Q12_MIN, Q12_MAX
        );
      }
      world.yarn_vx_q12 = clampInt(world.yarn_vx_q12 * state.settings.damping, Q12_MIN, Q12_MAX);
      if (state.yarnGrounded && Math.abs(world.yarn_vx_q12) < 24) world.yarn_vx_q12 = 0;
      world.yarn_x_q16 += world.yarn_vx_q12 / 4096 * Q16_MAX * dt;
      world.yarn_y_q16 += world.yarn_vy_q12 / 4096 * Q16_MAX * dt;
      var radius = state.settings.yarnRadiusQ16;
      var floor = Q16_MAX - state.settings.floorInsetQ16;
      if (world.yarn_x_q16 < radius || world.yarn_x_q16 > Q16_MAX - radius) {
        world.yarn_x_q16 = clamp(world.yarn_x_q16, radius, Q16_MAX - radius);
        world.yarn_vx_q12 = clampInt(-world.yarn_vx_q12 * state.settings.bounce, Q12_MIN, Q12_MAX);
      }
      if (world.yarn_y_q16 < radius || world.yarn_y_q16 > floor) {
        world.yarn_y_q16 = clamp(world.yarn_y_q16, radius, floor);
        if (world.yarn_y_q16 >= floor && world.yarn_vy_q12 >= 0) {
          var bouncedVy = clampInt(-world.yarn_vy_q12 * state.settings.bounce, Q12_MIN, Q12_MAX);
          if (Math.abs(bouncedVy) < 90) {
            world.yarn_vy_q12 = 0;
            state.yarnGrounded = true;
          } else {
            world.yarn_vy_q12 = bouncedVy;
            state.yarnGrounded = false;
          }
        } else {
          world.yarn_vy_q12 = clampInt(-world.yarn_vy_q12 * state.settings.bounce, Q12_MIN, Q12_MAX);
          state.yarnGrounded = false;
        }
      }
      changed = Math.abs(world.yarn_vx_q12) > 3 || Math.abs(world.yarn_vy_q12) > 3
        || wasGrounded !== state.yarnGrounded;
    }
    var collision = collideYarnWithPose('self', state.poses.self);
    collision = collideYarnWithPose('peer', state.poses.peer) || collision;
    collision = detectCatCollision() || collision;
    if (world.yarn_active) {
      world.yarn_x_q16 = clampInt(world.yarn_x_q16, 0, Q16_MAX);
      world.yarn_y_q16 = clampInt(world.yarn_y_q16, 0, Q16_MAX);
    }
    if (changed || collision || world.yarn_holder !== 'none') {
      renderWorld();
      publish('frame');
      scheduleSettleCommit();
    }
  }

  function startSimulation() {
    if (!state.active || state.ending || state.side !== 'host' || state.simulationStarted) return;
    state.simulationStarted = true;
    state.lastTickAt = 0;
    state.publishedWorldSeq = Math.max(state.publishedWorldSeq, state.lastAppliedWorldSeq);
    if (!state.tickTimer) state.tickTimer = setInterval(function () { simulate(Date.now()); }, TICK_MS);
    renderWorld();
    publish('commit');
    var pending = state.pendingIntents.splice(0, state.pendingIntents.length);
    pending.forEach(applyHostIntent);
  }

  function validWorldFrame(frame) {
    if (!frame || frame.type !== 'visit_cat_world' || frame.visit_id !== state.visitId
        || (frame.phase !== 'frame' && frame.phase !== 'commit')
        || !Number.isSafeInteger(frame.world_seq) || frame.world_seq < 1 || frame.world_seq > U32_MAX
        || typeof frame.yarn_active !== 'boolean' || !validQ16(frame.yarn_x_q16)
        || !validQ16(frame.yarn_y_q16) || !Number.isSafeInteger(frame.yarn_vx_q12)
        || frame.yarn_vx_q12 < Q12_MIN || frame.yarn_vx_q12 > Q12_MAX
        || !Number.isSafeInteger(frame.yarn_vy_q12) || frame.yarn_vy_q12 < Q12_MIN
        || frame.yarn_vy_q12 > Q12_MAX || ['none', 'host', 'guest'].indexOf(frame.yarn_holder) < 0
        || typeof frame.question_active !== 'boolean' || !validQ16(frame.question_x_q16)
        || !validQ16(frame.question_y_q16) || !Number.isSafeInteger(frame.collision_seq)
        || frame.collision_seq < 0 || frame.collision_seq > U32_MAX) return false;
    return true;
  }

  function applyWorldFrame(frame) {
    if (!state.active || !validWorldFrame(frame) || frame.world_seq <= state.lastAppliedWorldSeq) return false;
    var previousCollision = state.world.collision_seq;
    var previousPublished = state.publishedWorldSeq;
    state.lastAppliedWorldSeq = frame.world_seq;
    state.publishedWorldSeq = Math.max(state.publishedWorldSeq, frame.world_seq);
    // A host sees both its own transport echo and cached recovery commits.  A
    // frame no newer than what this controller already published is an echo;
    // a higher cursor is authoritative recovery and must replace the default
    // world even if it arrived after the short replay wait.
    if (state.side === 'host' && state.simulationStarted
        && frame.world_seq <= previousPublished) return true;
    state.world = {
      world_seq: frame.world_seq,
      yarn_active: frame.yarn_active,
      yarn_x_q16: frame.yarn_x_q16,
      yarn_y_q16: frame.yarn_y_q16,
      yarn_vx_q12: frame.yarn_vx_q12,
      yarn_vy_q12: frame.yarn_vy_q12,
      yarn_holder: frame.yarn_holder,
      question_active: frame.question_active,
      question_x_q16: frame.question_x_q16,
      question_y_q16: frame.question_y_q16,
      collision_seq: frame.collision_seq
    };
    state.yarnGrounded = frame.yarn_holder === 'none' && frame.yarn_vy_q12 === 0
      && frame.yarn_y_q16 >= Q16_MAX - state.settings.floorInsetQ16;
    if (frame.collision_seq !== previousCollision) pulseCollision('');
    renderWorld();
    return true;
  }

  function validPose(frame) {
    return !!(frame && frame.type === 'visit_cat_pose' && frame.visit_id === state.visitId
      && (frame.actor === 'self' || frame.actor === 'peer')
      && validQ16(frame.x_q16) && validQ16(frame.y_q16)
      && Number.isSafeInteger(frame.motion_id) && Number.isSafeInteger(frame.sample_seq));
  }

  function onPose(event) {
    var frame = event && event.detail ? event.detail : event;
    if (!state.active || !validPose(frame)) return;
    var previous = state.poses[frame.actor];
    if (previous && (frame.motion_id < previous.motion_id
        || (frame.motion_id === previous.motion_id && frame.sample_seq <= previous.sample_seq))) return;
    state.poses[frame.actor] = {
      x_q16: frame.x_q16,
      y_q16: frame.y_q16,
      motion_id: frame.motion_id,
      sample_seq: frame.sample_seq
    };
  }

  function onWorldIntent(event) {
    var frame = event && event.detail ? event.detail : event;
    applyHostIntent(frame);
  }

  function onWorld(event) {
    var frame = event && event.detail ? event.detail : event;
    applyWorldFrame(frame);
  }

  function clearTimers() {
    if (state.tickTimer) clearInterval(state.tickTimer);
    if (state.startTimer) clearTimeout(state.startTimer);
    if (state.settleTimer) clearTimeout(state.settleTimer);
    if (state.collisionPulseTimer) clearTimeout(state.collisionPulseTimer);
    state.tickTimer = 0;
    state.startTimer = 0;
    state.settleTimer = 0;
    state.collisionPulseTimer = 0;
    if (state.layer) state.layer.classList.remove('is-collision');
  }

  function stopForEnding() {
    state.ending = true;
    clearTimers();
    state.simulationStarted = false;
    state.lastTickAt = 0;
    state.pendingIntents = [];
    state.drag = null;
    state.dragAuthority = null;
    state.outbound = [];
    state.outboundBusy = false;
    state.outboundToken = nextU32(state.outboundToken);
  }

  function scheduleSimulationStart() {
    if (!state.active || state.ending || state.side !== 'host'
        || state.simulationStarted || state.startTimer) return;
    state.startTimer = setTimeout(function () {
      state.startTimer = 0;
      startSimulation();
    }, REPLAY_WAIT_MS);
  }

  function deactivate() {
    clearTimers();
    state.active = false;
    state.ending = false;
    state.visitId = '';
    state.side = '';
    state.world = defaultWorld();
    state.poses = { self: null, peer: null };
    state.lastAppliedWorldSeq = 0;
    state.publishedWorldSeq = 0;
    state.simulationStarted = false;
    state.yarnGrounded = false;
    state.lastTickAt = 0;
    state.catOverlap = false;
    state.yarnContacts = { self: false, peer: false };
    state.drag = null;
    state.dragAuthority = null;
    state.pointerLastSentAt = 0;
    state.intentToken = nextU32(state.intentToken);
    state.intentQueue = Promise.resolve();
    state.pendingIntents = [];
    state.outbound = [];
    state.outboundBusy = false;
    state.outboundToken = nextU32(state.outboundToken);
    removeLayer();
  }

  function activate(frame) {
    var sameVisit = state.active && state.visitId === frame.visit_id;
    if (!sameVisit) deactivate();
    state.active = true;
    state.ending = false;
    state.visitId = frame.visit_id;
    state.side = frame.side;
    state.settings = readSettings();
    ensureLayer();
    scheduleSimulationStart();
  }

  function onDisplayState(event) {
    var frame = event && event.detail ? event.detail : event;
    if (!frame || frame.type !== 'visit_state_change'
        || !VISIT_ID_RE.test(String(frame.visit_id || ''))) return;
    var liveAction = ['started', 'departed', 'wrap_up', 'ending', 'reconnecting'].indexOf(frame.action) !== -1;
    if (frame.cat_scene === 2 && liveAction
        && (frame.side === 'host' || frame.side === 'guest')
        && (!state.active || state.visitId !== frame.visit_id)) {
      activate(frame);
    }
    if (!state.active || frame.visit_id !== state.visitId) return;
    if (frame.action === 'ended') deactivate();
    else if (frame.action === 'ending') stopForEnding();
    else if (frame.action === 'started' || frame.action === 'departed') {
      state.ending = false;
      scheduleSimulationStart();
    }
  }

  function onSceneDeactivated(event) {
    var detail = event && event.detail ? event.detail : event;
    if (!state.active || !detail || detail.visit_id !== state.visitId) return;
    deactivate();
  }

  function snapshot() {
    return Object.freeze({
      active: state.active,
      ending: state.ending,
      visit_id: state.visitId,
      side: state.side,
      simulation_started: state.simulationStarted,
      last_applied_world_seq: state.lastAppliedWorldSeq,
      published_world_seq: state.publishedWorldSeq,
      world: frozenCopy(state.world),
      self_pose: state.poses.self ? frozenCopy(state.poses.self) : null,
      peer_pose: state.poses.peer ? frozenCopy(state.poses.peer) : null
    });
  }

  global.addEventListener('neko-visit-display-state', onDisplayState);
  global.addEventListener('neko-visit-cat-pose', onPose);
  global.addEventListener('neko-visit-cat-world-intent', onWorldIntent);
  global.addEventListener('neko-visit-cat-world', onWorld);
  global.addEventListener('neko:visit-cat-scene-deactivated', onSceneDeactivated);
  global.addEventListener('pagehide', function () {
    global.removeEventListener('neko-visit-display-state', onDisplayState);
    global.removeEventListener('neko-visit-cat-pose', onPose);
    global.removeEventListener('neko-visit-cat-world-intent', onWorldIntent);
    global.removeEventListener('neko-visit-cat-world', onWorld);
    global.removeEventListener('neko:visit-cat-scene-deactivated', onSceneDeactivated);
    deactivate();
  }, { once: true });

  global.NekoVisitCatWorld = Object.freeze({
    isActive: function () { return state.active; },
    snapshot: snapshot
  });

  if (typeof global.nekoVisitDisplayStateSnapshot === 'function') {
    var initial = global.nekoVisitDisplayStateSnapshot();
    if (initial) onDisplayState(initial);
  }
  if (typeof global.nekoVisitCatPoseSnapshots === 'function') {
    var initialPoses = global.nekoVisitCatPoseSnapshots();
    if (Array.isArray(initialPoses)) initialPoses.forEach(onPose);
  }
  if (typeof global.nekoVisitCatWorldSnapshot === 'function') {
    var initialWorld = global.nekoVisitCatWorldSnapshot();
    if (initialWorld) onWorld(initialWorld);
  }
  if (typeof global.nekoVisitCatWorldIntentSnapshots === 'function') {
    var initialIntents = global.nekoVisitCatWorldIntentSnapshots();
    if (Array.isArray(initialIntents)) initialIntents.forEach(onWorldIntent);
  }
})(typeof window !== 'undefined' ? window : globalThis);
