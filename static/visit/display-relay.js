/**
 * 串门展示帧的跨窗口桥。
 *
 * Electron 多窗口模式里，/chat 跟随窗通常持有最新的主 WebSocket，真正负责
 * 截图和媒体传输的却是 index Pet 窗。把 visit_state_change 复制给 Pet 窗，
 * 避免邀请已经建好、传输 iframe 却一直没有启动。猫形态动作也沿用这条
 * 页面桥，但只传受限字段，并汇合 Pet 直达与 Chat 中继的重复帧。
 */
(function () {
  'use strict';

  var CHANNEL_NAME = 'neko_visit_display_v1';
  var isChatFollower = /^\/chat(?:_full)?(?:\/|$)/.test(
    (window.location && window.location.pathname) || ''
  );
  var channel = null;
  var CAT_SCENE_HISTORY_MAX = 256;
  var catSceneKeys = new Set();
  var catSceneOrder = [];
  var CAT_POSE_HISTORY_MAX = 512;
  var catPoseKeys = new Set();
  var catPoseOrder = [];
  var CAT_WORLD_HISTORY_MAX = 512;
  var catWorldKeys = new Set();
  var catWorldOrder = [];
  var lastDisplayState = null;
  var retainedCatStart = null;
  var retainedCatPhase = null;
  var retainedCatPoses = { self: null, peer: null };
  var retainedCatWorld = null;
  var retainedCatWorldIntents = [];

  function currentCatgirl() {
    var config = window.lanlan_config;
    return config && typeof config.lanlan_name === 'string' ? config.lanlan_name : '';
  }

  function safeFrame(frame) {
    if (!frame || frame.type !== 'visit_state_change') return null;
    var copy = { type: 'visit_state_change' };
    [
      'action', 'side', 'visit_id', 'ts', 'peer_crop', 'ends_at',
      'reason', 'transport'
    ].forEach(function (key) {
      var value = frame[key];
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        copy[key] = value;
      }
    });
    if (frame.cat_scene === 2) copy.cat_scene = 2;
    ['self_cat_asset', 'peer_cat_asset'].forEach(function (key) {
      var value = frame[key];
      if (value === 'dev_neko' || value === 'yui_neko') copy[key] = value;
    });
    if (typeof copy.action !== 'string' || typeof copy.visit_id !== 'string') return null;
    return copy;
  }

  function safeCatFrame(frame) {
    if (!frame || frame.type !== 'visit_cat_scene') return null;
    if (typeof frame.visit_id !== 'string'
        || !/^[A-Za-z0-9_-]{22}$/.test(frame.visit_id)) return null;
    if (frame.actor !== 'self' && frame.actor !== 'peer') return null;
    if (frame.kind !== 'greet' && frame.kind !== 'play' && frame.kind !== 'rest' && frame.kind !== 'move') return null;
    if (!Number.isSafeInteger(frame.seq) || frame.seq < 1 || frame.seq > 0xffffffff) return null;
    if (!Number.isSafeInteger(frame.seed) || frame.seed < 0 || frame.seed > 0xffffffff) return null;
    // Copy only the scene contract. Unknown future fields never cross the
    // BroadcastChannel boundary, but do not make an otherwise valid frame fail.
    return {
      type: 'visit_cat_scene',
      visit_id: frame.visit_id,
      actor: frame.actor,
      kind: frame.kind,
      seq: frame.seq,
      seed: frame.seed
    };
  }

  var CAT_POSE_SLOT_TIERS = {
    'idle.cat1': 'cat1', 'idle.cat2': 'cat2', 'idle.cat3': 'cat3',
    'click.cat1': 'cat1', 'click.cat2': 'cat2', 'click.cat3': 'cat3',
    'drag.cat1': 'cat1', 'drag.cat2': 'cat2', 'drag.cat3': 'cat3',
    'drag.rapid': 'cat1',
    'movement.cat1.walking': 'cat1', 'movement.cat1.stretch': 'cat1',
    'movement.cat1.interactive': 'cat1', 'action.cat1.eat': 'cat1',
    'action.cat1.play_yarn': 'cat1', 'playground.cat1.air': 'cat1'
  };

  function safeCatPoseFrame(frame) {
    if (!frame || frame.type !== 'visit_cat_pose') return null;
    if (typeof frame.visit_id !== 'string'
        || !/^[A-Za-z0-9_-]{22}$/.test(frame.visit_id)) return null;
    if (frame.actor !== 'self' && frame.actor !== 'peer') return null;
    if (frame.phase !== 'sample' && frame.phase !== 'commit') return null;
    if (!Number.isSafeInteger(frame.motion_id) || frame.motion_id < 1 || frame.motion_id > 0xffffffff
        || !Number.isSafeInteger(frame.sample_seq) || frame.sample_seq < 1 || frame.sample_seq > 0xffffffff
        || !Number.isSafeInteger(frame.x_q16) || frame.x_q16 < 0 || frame.x_q16 > 65535
        || !Number.isSafeInteger(frame.y_q16) || frame.y_q16 < 0 || frame.y_q16 > 65535) return null;
    if (['idle', 'walk', 'drag', 'airborne', 'edge', 'follow'].indexOf(frame.mode) === -1
        || (frame.facing !== 'left' && frame.facing !== 'right')
        || CAT_POSE_SLOT_TIERS[frame.slot] !== frame.tier) return null;
    var copy = {
      type: 'visit_cat_pose',
      visit_id: frame.visit_id,
      actor: frame.actor,
      phase: frame.phase,
      motion_id: frame.motion_id,
      sample_seq: frame.sample_seq,
      x_q16: frame.x_q16,
      y_q16: frame.y_q16,
      mode: frame.mode,
      facing: frame.facing,
      tier: frame.tier,
      slot: frame.slot
    };
    if (Number.isSafeInteger(frame.seq) && frame.seq >= 1 && frame.seq <= 0xffffffff) {
      copy.seq = frame.seq;
    }
    return copy;
  }

  function safeCatWorldIntentFrame(frame) {
    if (!frame || frame.type !== 'visit_cat_world_intent') return null;
    if (typeof frame.visit_id !== 'string'
        || !/^[A-Za-z0-9_-]{22}$/.test(frame.visit_id)) return null;
    if (frame.actor !== 'self' && frame.actor !== 'peer') return null;
    if (['yarn_spawn', 'yarn_grab', 'yarn_release', 'question_tap', 'pointer'].indexOf(frame.kind) < 0
        || !Number.isSafeInteger(frame.intent_id) || frame.intent_id < 1 || frame.intent_id > 0xffffffff
        || !Number.isSafeInteger(frame.x_q16) || frame.x_q16 < 0 || frame.x_q16 > 65535
        || !Number.isSafeInteger(frame.y_q16) || frame.y_q16 < 0 || frame.y_q16 > 65535) return null;
    return {
      type: 'visit_cat_world_intent',
      visit_id: frame.visit_id,
      actor: frame.actor,
      kind: frame.kind,
      intent_id: frame.intent_id,
      x_q16: frame.x_q16,
      y_q16: frame.y_q16
    };
  }

  function safeCatWorldFrame(frame) {
    if (!frame || frame.type !== 'visit_cat_world') return null;
    if (typeof frame.visit_id !== 'string'
        || !/^[A-Za-z0-9_-]{22}$/.test(frame.visit_id)
        || (frame.phase !== 'frame' && frame.phase !== 'commit')
        || !Number.isSafeInteger(frame.world_seq) || frame.world_seq < 1 || frame.world_seq > 0xffffffff
        || typeof frame.yarn_active !== 'boolean'
        || !Number.isSafeInteger(frame.yarn_x_q16) || frame.yarn_x_q16 < 0 || frame.yarn_x_q16 > 65535
        || !Number.isSafeInteger(frame.yarn_y_q16) || frame.yarn_y_q16 < 0 || frame.yarn_y_q16 > 65535
        || !Number.isSafeInteger(frame.yarn_vx_q12) || frame.yarn_vx_q12 < -4096 || frame.yarn_vx_q12 > 4095
        || !Number.isSafeInteger(frame.yarn_vy_q12) || frame.yarn_vy_q12 < -4096 || frame.yarn_vy_q12 > 4095
        || ['none', 'host', 'guest'].indexOf(frame.yarn_holder) < 0
        || typeof frame.question_active !== 'boolean'
        || !Number.isSafeInteger(frame.question_x_q16) || frame.question_x_q16 < 0 || frame.question_x_q16 > 65535
        || !Number.isSafeInteger(frame.question_y_q16) || frame.question_y_q16 < 0 || frame.question_y_q16 > 65535
        || !Number.isSafeInteger(frame.collision_seq) || frame.collision_seq < 0
        || frame.collision_seq > 0xffffffff) return null;
    var copy = {
      type: 'visit_cat_world',
      visit_id: frame.visit_id,
      phase: frame.phase,
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
    if (Number.isSafeInteger(frame.seq) && frame.seq >= 1 && frame.seq <= 0xffffffff) {
      copy.seq = frame.seq;
    }
    return copy;
  }

  function catSceneKey(frame) {
    return frame.visit_id + '\u0000' + frame.actor + '\u0000' + String(frame.seq);
  }

  function rememberCatScene(frame) {
    var key = catSceneKey(frame);
    if (catSceneKeys.has(key)) return false;
    catSceneKeys.add(key);
    catSceneOrder.push(key);
    while (catSceneOrder.length > CAT_SCENE_HISTORY_MAX) {
      catSceneKeys.delete(catSceneOrder.shift());
    }
    return true;
  }

  function dispatchCatScene(frame) {
    if (!rememberCatScene(frame)) return false;
    try {
      window.dispatchEvent(new CustomEvent('neko-visit-cat-scene', { detail: frame }));
      return true;
    } catch (_) {
      return false;
    }
  }

  function catPoseKey(frame) {
    return frame.visit_id + '\u0000' + frame.actor + '\u0000' + frame.phase
      + '\u0000' + String(frame.motion_id) + '\u0000' + String(frame.sample_seq);
  }

  function rememberCatPose(frame) {
    var key = catPoseKey(frame);
    if (catPoseKeys.has(key)) return false;
    catPoseKeys.add(key);
    catPoseOrder.push(key);
    while (catPoseOrder.length > CAT_POSE_HISTORY_MAX) {
      catPoseKeys.delete(catPoseOrder.shift());
    }
    return true;
  }

  function dispatchCatPose(frame) {
    var previous = retainedCatPoses[frame.actor];
    if (!previous || previous.visit_id !== frame.visit_id
        || frame.motion_id > previous.motion_id
        || (frame.motion_id === previous.motion_id && frame.sample_seq > previous.sample_seq)) {
      retainedCatPoses[frame.actor] = Object.assign({}, frame);
    }
    if (!rememberCatPose(frame)) return false;
    try {
      window.dispatchEvent(new CustomEvent('neko-visit-cat-pose', { detail: frame }));
      return true;
    } catch (_) {
      return false;
    }
  }

  function rememberCatWorld(key) {
    if (catWorldKeys.has(key)) return false;
    catWorldKeys.add(key);
    catWorldOrder.push(key);
    while (catWorldOrder.length > CAT_WORLD_HISTORY_MAX) {
      catWorldKeys.delete(catWorldOrder.shift());
    }
    return true;
  }

  function dispatchCatWorldIntent(frame) {
    var key = frame.visit_id + '\u0000' + frame.actor + '\u0000' + frame.kind
      + '\u0000' + String(frame.intent_id);
    if (frame.kind === 'pointer') key += '\u0000' + frame.x_q16 + '\u0000' + frame.y_q16;
    if (!rememberCatWorld('intent\u0000' + key)) return false;
    if (frame.kind !== 'pointer') {
      retainedCatWorldIntents.push(Object.assign({}, frame));
      while (retainedCatWorldIntents.length > 64) retainedCatWorldIntents.shift();
    }
    try {
      window.dispatchEvent(new CustomEvent('neko-visit-cat-world-intent', { detail: frame }));
      return true;
    } catch (_) {
      return false;
    }
  }

  function dispatchCatWorld(frame) {
    var key = 'world\u0000' + frame.visit_id + '\u0000' + frame.phase
      + '\u0000' + String(frame.world_seq);
    if (!retainedCatWorld || retainedCatWorld.visit_id !== frame.visit_id
        || frame.world_seq > retainedCatWorld.world_seq) {
      retainedCatWorld = Object.assign({}, frame);
    }
    if (!rememberCatWorld(key)) return false;
    try {
      window.dispatchEvent(new CustomEvent('neko-visit-cat-world', { detail: frame }));
      return true;
    } catch (_) {
      return false;
    }
  }

  function dispatchDisplayState(frame) {
    var normalized = safeFrame(frame);
    if (!normalized) return false;
    lastDisplayState = normalized;
    if (normalized.action === 'started' && normalized.cat_scene === 2) {
      retainedCatStart = Object.assign({}, normalized);
      retainedCatPhase = Object.assign({}, normalized);
      retainedCatPoses.self = null;
      retainedCatPoses.peer = null;
      retainedCatWorld = null;
      retainedCatWorldIntents = [];
    } else if (retainedCatStart && retainedCatStart.visit_id === normalized.visit_id) {
      if (normalized.action === 'ended') {
        retainedCatStart = null;
        retainedCatPhase = null;
        retainedCatPoses.self = null;
        retainedCatPoses.peer = null;
        retainedCatWorld = null;
        retainedCatWorldIntents = [];
      } else if (['departed', 'wrap_up', 'ending', 'reconnecting', 'started'].indexOf(normalized.action) !== -1) {
        retainedCatPhase = Object.assign({}, retainedCatStart, normalized);
      }
    } else if (retainedCatStart && retainedCatStart.visit_id !== normalized.visit_id
        && ['pending', 'invite_ready', 'started'].indexOf(normalized.action) !== -1) {
      retainedCatStart = null;
      retainedCatPhase = null;
      retainedCatPoses.self = null;
      retainedCatPoses.peer = null;
      retainedCatWorld = null;
      retainedCatWorldIntents = [];
    }
    try {
      window.dispatchEvent(new CustomEvent('neko-visit-display-state', { detail: normalized }));
      return true;
    } catch (_) {
      return false;
    }
  }

  window.nekoVisitDisplayStateSnapshot = function nekoVisitDisplayStateSnapshot() {
    var snapshot = retainedCatPhase || lastDisplayState;
    return snapshot ? Object.assign({}, snapshot) : null;
  };

  window.nekoVisitCatPoseSnapshots = function nekoVisitCatPoseSnapshots() {
    return ['self', 'peer'].map(function (actor) {
      return retainedCatPoses[actor] ? Object.assign({}, retainedCatPoses[actor]) : null;
    }).filter(Boolean);
  };

  window.nekoVisitCatWorldSnapshot = function nekoVisitCatWorldSnapshot() {
    return retainedCatWorld ? Object.assign({}, retainedCatWorld) : null;
  };

  window.nekoVisitCatWorldIntentSnapshots = function nekoVisitCatWorldIntentSnapshots() {
    var snapshots = retainedCatWorldIntents.map(function (frame) { return Object.assign({}, frame); });
    retainedCatWorldIntents = [];
    return snapshots;
  };

  try {
    if (typeof BroadcastChannel === 'function') channel = new BroadcastChannel(CHANNEL_NAME);
  } catch (_) {
    channel = null;
  }

  window.nekoRelayVisitStateToDisplay = function relayVisitStateToDisplay(frame) {
    if (!isChatFollower || !channel) return false;
    var normalized = safeFrame(frame);
    if (!normalized) return false;
    try {
      channel.postMessage({
        type: 'visit_display_state',
        catgirl: currentCatgirl(),
        frame: normalized
      });
      return true;
    } catch (_) {
      return false;
    }
  };

  function relayCatSceneToDisplay(frame) {
    if (!isChatFollower || !channel) return false;
    var normalized = safeCatFrame(frame);
    var catgirl = currentCatgirl();
    if (!normalized || !catgirl) return false;
    try {
      channel.postMessage({
        type: 'visit_display_cat_scene',
        catgirl: catgirl,
        frame: normalized
      });
      return true;
    } catch (_) {
      return false;
    }
  }

  function relayCatPoseToDisplay(frame) {
    var normalized = safeCatPoseFrame(frame);
    if (!normalized) return false;
    if (!isChatFollower) return dispatchCatPose(normalized);
    var catgirl = currentCatgirl();
    if (!channel || !catgirl) return false;
    try {
      channel.postMessage({
        type: 'visit_display_cat_pose',
        catgirl: catgirl,
        frame: normalized
      });
      return true;
    } catch (_) {
      return false;
    }
  }

  window.nekoRelayVisitCatPoseToDisplay = relayCatPoseToDisplay;

  function relayCatWorldIntentToDisplay(frame) {
    var normalized = safeCatWorldIntentFrame(frame);
    if (!normalized) return false;
    if (!isChatFollower) return dispatchCatWorldIntent(normalized);
    var catgirl = currentCatgirl();
    if (!channel || !catgirl) return false;
    try {
      channel.postMessage({
        type: 'visit_display_cat_world_intent',
        catgirl: catgirl,
        frame: normalized
      });
      return true;
    } catch (_) {
      return false;
    }
  }

  function relayCatWorldToDisplay(frame) {
    var normalized = safeCatWorldFrame(frame);
    if (!normalized) return false;
    if (!isChatFollower) return dispatchCatWorld(normalized);
    var catgirl = currentCatgirl();
    if (!channel || !catgirl) return false;
    try {
      channel.postMessage({
        type: 'visit_display_cat_world',
        catgirl: catgirl,
        frame: normalized
      });
      return true;
    } catch (_) {
      return false;
    }
  }

  window.nekoRelayVisitCatWorldIntentToDisplay = relayCatWorldIntentToDisplay;
  window.nekoRelayVisitCatWorldToDisplay = relayCatWorldToDisplay;

  function onVisitFrame(event) {
    var raw = event && event.detail;
    var stateFrame = safeFrame(raw);
    if (stateFrame) {
      // Chat applies and relays lifecycle frames from visit-chat.js. Pet/Web
      // owns the visible scene and can publish its already local frame now.
      if (!isChatFollower) dispatchDisplayState(stateFrame);
      return;
    }
    var sceneFrame = safeCatFrame(raw);
    if (sceneFrame) {
      if (isChatFollower) relayCatSceneToDisplay(sceneFrame);
      else dispatchCatScene(sceneFrame);
      return;
    }
    var poseFrame = safeCatPoseFrame(raw);
    if (poseFrame) {
      relayCatPoseToDisplay(poseFrame);
      return;
    }
    var worldIntentFrame = safeCatWorldIntentFrame(raw);
    if (worldIntentFrame) {
      relayCatWorldIntentToDisplay(worldIntentFrame);
      return;
    }
    var worldFrame = safeCatWorldFrame(raw);
    if (worldFrame) relayCatWorldToDisplay(worldFrame);
  }

  window.addEventListener('neko-visit-frame', onVisitFrame);

  if (!isChatFollower && channel) {
    channel.onmessage = function (event) {
      var payload = event && event.data;
      if (!payload) return;
      if (payload.type === 'visit_display_cat_scene') {
        var sceneCatgirl = currentCatgirl();
        if (!sceneCatgirl || typeof payload.catgirl !== 'string'
            || payload.catgirl !== sceneCatgirl) return;
        var scene = safeCatFrame(payload.frame);
        if (scene) dispatchCatScene(scene);
        return;
      }
      if (payload.type === 'visit_display_cat_pose') {
        var poseCatgirl = currentCatgirl();
        if (!poseCatgirl || typeof payload.catgirl !== 'string'
            || payload.catgirl !== poseCatgirl) return;
        var pose = safeCatPoseFrame(payload.frame);
        if (pose) dispatchCatPose(pose);
        return;
      }
      if (payload.type === 'visit_display_cat_world_intent') {
        var intentCatgirl = currentCatgirl();
        if (!intentCatgirl || typeof payload.catgirl !== 'string'
            || payload.catgirl !== intentCatgirl) return;
        var intent = safeCatWorldIntentFrame(payload.frame);
        if (intent) dispatchCatWorldIntent(intent);
        return;
      }
      if (payload.type === 'visit_display_cat_world') {
        var worldCatgirl = currentCatgirl();
        if (!worldCatgirl || typeof payload.catgirl !== 'string'
            || payload.catgirl !== worldCatgirl) return;
        var world = safeCatWorldFrame(payload.frame);
        if (world) dispatchCatWorld(world);
        return;
      }
      if (payload.type !== 'visit_display_state') return;
      var catgirl = currentCatgirl();
      if (payload.catgirl && catgirl && payload.catgirl !== catgirl) return;
      var frame = safeFrame(payload.frame);
      var bridge = window.nekoVisitParentBridge;
      if (!frame) return;
      dispatchDisplayState(frame);
      if (!bridge || typeof bridge.onStateChange !== 'function') return;
      try { bridge.onStateChange(frame); } catch (_) { /* 下一条权威状态仍可恢复 */ }
    };
  }

  window.addEventListener('pagehide', function () {
    window.removeEventListener('neko-visit-frame', onVisitFrame);
    if (channel && typeof channel.close === 'function') {
      try { channel.close(); } catch (_) { /* noop */ }
    }
    channel = null;
    lastDisplayState = null;
    retainedCatStart = null;
    retainedCatPhase = null;
    retainedCatPoses.self = null;
    retainedCatPoses.peer = null;
    retainedCatWorld = null;
    retainedCatWorldIntents = [];
    catSceneKeys.clear();
    catSceneOrder.length = 0;
    catPoseKeys.clear();
    catPoseOrder.length = 0;
    catWorldKeys.clear();
    catWorldOrder.length = 0;
  }, { once: true });
})();
