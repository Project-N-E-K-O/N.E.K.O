# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Browser contract for the host-authoritative cat-visit shared world."""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORLD_PATH = PROJECT_ROOT / "static" / "visit" / "cat-world.js"
PLAYGROUND_PATH = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons" / "idle-playground.js"

NODE_SCENARIO = r"""
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const SRC = fs.readFileSync(__WORLD_PATH__, 'utf8');
const VID = 'AAAAAAAAAAAAAAAAAAAAAA';

class CustomEventLike {
  constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
}

class NodeLike {
  constructor(tag) {
    this.tagName = String(tag || '').toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this.attrs = {};
    this.style = {};
    this.hidden = false;
    this.isConnected = true;
    this.className = '';
    this.classList = {
      values: new Set(),
      add: (...names) => names.forEach((name) => this.classList.values.add(name)),
      remove: (...names) => names.forEach((name) => this.classList.values.delete(name)),
      contains: (name) => this.classList.values.has(name),
    };
  }
  setAttribute(name, value) { this.attrs[name] = String(value); if (name === 'id') this.id = String(value); }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
  appendChild(child) { child.parentNode = this; child.isConnected = true; this.children.push(child); return child; }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    child.parentNode = null;
    child.isConnected = false;
    return child;
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    (this.listeners[event.type] || []).slice().forEach((fn) => fn(event));
    return true;
  }
  click() { this.dispatchEvent({ type: 'click', clientX: 400, clientY: 120 }); }
  setPointerCapture() {}
  all() { return [this].concat(this.children.flatMap((child) => child.all())); }
  querySelector(selector) {
    if (selector === '[data-neko-visit-cat-world]') {
      return this.all().find((node) => node.getAttribute('data-neko-visit-cat-world') !== null) || null;
    }
    if (selector === '[data-visit-cat-shared-object="question"]') {
      return this.all().find((node) => node.getAttribute('data-visit-cat-shared-object') === 'question') || null;
    }
    if (selector === '[data-visit-cat-shared-object="yarn"]') {
      return this.all().find((node) => node.getAttribute('data-visit-cat-shared-object') === 'yarn') || null;
    }
    return null;
  }
}

async function main() {
  let now = 1000;
  let nextTimer = 1;
  const timers = new Map();
  const listeners = new Map();
  const calls = { intent: [], pointer: [], state: [] };
  const geometry = [];
  let staleIntentOnce = false;
  let stateResponses = 0;
  let failStates = 0;
  let failIntents = 0;
  let loseIntentResponse = false;
  let acceptedLostIntent = 0;
  let refuseThenStaleSame = false;
  let refusedIntent = 0;
  const storage = new Map();
  let staleStateCursor = 0;
  let stateGate = null;

  function schedule(fn, delay, interval) {
    const id = nextTimer++;
    timers.set(id, { id, fn, at: now + Math.max(0, Number(delay) || 0), interval: interval || 0 });
    return id;
  }
  function cancel(id) { timers.delete(id); }
  async function flush() {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  }
  async function advance(ms) {
    const target = now + ms;
    while (true) {
      const due = Array.from(timers.values()).sort((a, b) => a.at - b.at || a.id - b.id)[0];
      if (!due || due.at > target) break;
      now = due.at;
      if (due.interval) due.at += due.interval;
      else timers.delete(due.id);
      due.fn();
      await flush();
    }
    now = target;
    await flush();
  }

  const body = new NodeLike('body');
  const scene = new NodeLike('section');
  scene.setAttribute('id', 'neko-visit-cat-scene');
  scene.setAttribute('data-neko-visit-cat-world', '');
  body.appendChild(scene);
  const documentShim = {
    body,
    documentElement: body,
    createElement: (tag) => new NodeLike(tag),
    getElementById: (id) => body.all().find((node) => node.id === id) || null,
    querySelector: (selector) => body.querySelector(selector),
  };
  const win = {
    document: documentShim,
    innerWidth: 1000,
    innerHeight: 800,
    CustomEvent: CustomEventLike,
    Date: { now: () => now },
    Math,
    Number,
    Object,
    Promise,
    setTimeout: (fn, delay) => schedule(fn, delay, 0),
    clearTimeout: cancel,
    sessionStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => { storage.set(key, String(value)); },
    },
    setInterval: (fn, delay) => schedule(fn, delay, Math.max(1, Number(delay) || 1)),
    clearInterval: cancel,
    addEventListener(type, fn) { (listeners.set(type, listeners.get(type) || []), listeners.get(type).push(fn)); },
    removeEventListener(type, fn) {
      const list = listeners.get(type) || [];
      const index = list.indexOf(fn);
      if (index >= 0) list.splice(index, 1);
    },
    dispatchEvent(event) {
      if (event.type === 'neko:visit-cat-geometry-changed') geometry.push(now);
      (listeners.get(event.type) || []).slice().forEach((fn) => fn(event));
      return true;
    },
    nekoVisitCatWorldSettings: {
      gravity_q12_per_second: 1000,
      bounce: 0.1,
      damping: 0.85,
    },
    nekoVisitCatWorldApi: {
      sendIntent(payload) {
        calls.intent.push(JSON.parse(JSON.stringify(payload)));
        if (failIntents > 0) {
          failIntents -= 1;
          return Promise.resolve(false);
        }
        if (refuseThenStaleSame) {
          refuseThenStaleSame = false;
          refusedIntent = payload.intent_id;
          return Promise.resolve({ ok: false, status: 409 });
        }
        if (refusedIntent && payload.intent_id === refusedIntent) {
          refusedIntent = 0;
          return Promise.resolve({ stale: true, current_intent_id: payload.intent_id });
        }
        if (loseIntentResponse) {
          loseIntentResponse = false;
          acceptedLostIntent = payload.intent_id;
          return Promise.resolve(false);
        }
        if (acceptedLostIntent && payload.intent_id === acceptedLostIntent) {
          acceptedLostIntent = 0;
          return Promise.resolve({ stale: true, current_intent_id: payload.intent_id });
        }
        if (staleIntentOnce) {
          staleIntentOnce = false;
          return Promise.resolve({ stale: true, current_intent_id: 40 });
        }
        return Promise.resolve({ ok: true });
      },
      sendPointer(payload) { calls.pointer.push(JSON.parse(JSON.stringify(payload))); return Promise.resolve(true); },
      sendState(payload) {
        calls.state.push(JSON.parse(JSON.stringify(payload)));
        stateResponses += 1;
        if (staleStateCursor) {
          const cursor = staleStateCursor;
          staleStateCursor = 0;
          const reply = { stale: true, current_world_seq: cursor };
          return stateGate ? stateGate.then(() => reply) : Promise.resolve(reply);
        }
        if (failStates > 0) {
          failStates -= 1;
          return Promise.resolve(false);
        }
        return Promise.resolve(stateResponses === 1
          ? { stale: true, current_world_seq: 40 }
          : { ok: true });
      },
    },
  };
  win.window = win;
  win.globalThis = win;
  const context = vm.createContext(win);
  vm.runInContext(SRC, context, { filename: 'cat-world.js' });
  const emit = (type, detail) => win.dispatchEvent(new CustomEventLike(type, { detail }));
  const lifecycle = (action, side) => emit('neko-visit-display-state', {
    type: 'visit_state_change', action, visit_id: VID, side, cat_scene: 2,
  });

  // Guest renders and submits intent, but never starts a simulator or publishes state.
  lifecycle('started', 'guest');
  const question = scene.querySelector('[data-visit-cat-shared-object="question"]');
  assert.ok(question);
  assert.equal(question.getAttribute('data-neko-visit-cat-input'), 'object');
  assert.equal(question.hidden, true);
  assert.equal(win.NekoVisitCatWorld.snapshot().world.question_active, false);

  // Gravity already consumes the ordinary Playground entry, so its first
  // activation also makes the shared yarn available without another control.
  emit('neko:idle-cat1-playground-entry-request', {
    source: 'gravity-cat-setting', trigger: 'gravity-cat-setting',
  });
  await flush();
  assert.equal(calls.intent.length, 1);
  assert.equal(calls.intent[0].kind, 'yarn_spawn');
  emit('neko-visit-cat-world', {
    type: 'visit_cat_world', visit_id: VID, phase: 'commit', world_seq: 1,
    yarn_active: true, yarn_x_q16: 32768, yarn_y_q16: 19661,
    yarn_vx_q12: 0, yarn_vy_q12: 0, yarn_holder: 'none',
    question_active: false, question_x_q16: 32768, question_y_q16: 13107,
    collision_seq: 0,
  });
  emit('neko:idle-cat1-playground-entry-request', {
    source: 'gravity-cat-setting', trigger: 'gravity-cat-setting',
  });
  await flush();
  assert.equal(calls.intent.length, 1);

  // The existing visible Playground entry remains the non-gravity spawn.
  emit('neko-visit-cat-world', {
    type: 'visit_cat_world', visit_id: VID, phase: 'commit', world_seq: 2,
    yarn_active: false, yarn_x_q16: 32768, yarn_y_q16: 19661,
    yarn_vx_q12: 0, yarn_vy_q12: 0, yarn_holder: 'none',
    question_active: false, question_x_q16: 32768, question_y_q16: 13107,
    collision_seq: 0,
  });
  emit('neko:idle-cat1-playground-entry-request', {
    source: 'question-mark', trigger: 'cat1-question-mark',
  });
  await flush();
  assert.equal(calls.intent.length, 2);
  assert.equal(calls.intent[1].kind, 'yarn_spawn');

  // Ordinary cat visits do not create a question block.  A newer peer may
  // still explicitly publish the legacy field, which remains wire-compatible.
  emit('neko-visit-cat-world', {
    type: 'visit_cat_world', visit_id: VID, phase: 'commit', world_seq: 3,
    yarn_active: false, yarn_x_q16: 32768, yarn_y_q16: 19661,
    yarn_vx_q12: 0, yarn_vy_q12: 0, yarn_holder: 'none',
    question_active: true, question_x_q16: 32768, question_y_q16: 13107,
    collision_seq: 0,
  });
  assert.equal(question.hidden, false);
  staleIntentOnce = true;
  question.click();
  await advance(400);
  assert.equal(calls.intent.length, 4);
  assert.equal(calls.intent[2].kind, 'question_tap');
  assert.equal(calls.intent[3].intent_id, 41);
  assert.equal(calls.state.length, 0);
  assert.equal(win.NekoVisitCatWorld.snapshot().simulation_started, false);

  // Host waits briefly for a cached commit, then publishes its deterministic initial world.
  lifecycle('ended', 'guest');
  // A retained active-phase snapshot must also activate a late-loaded world.
  lifecycle('departed', 'host');
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'question_tap',
    intent_id: 6, x_q16: 30000, y_q16: 15000,
  });
  await advance(181);
  assert.equal(win.NekoVisitCatWorld.snapshot().simulation_started, true);
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 30000);
  assert.ok(calls.state.some((item) => item.phase === 'commit'));
  // The first commit came back stale at a cursor this page never applied: it
  // waits for that recovery commit instead of overwriting it with its default.
  const staleWaitCount = calls.state.length;
  assert.equal(win.NekoVisitCatWorld.snapshot().published_world_seq, 40);

  // A cached recovery commit may arrive after the replay wait.  A cursor newer
  // than this host's publications replaces the default world; a later old echo cannot.
  function worldFrame(seq, x, phase = 'frame') {
    return {
      type: 'visit_cat_world', visit_id: VID, phase, world_seq: seq,
      yarn_active: true, yarn_x_q16: x, yarn_y_q16: 20000,
      yarn_vx_q12: 0, yarn_vy_q12: 0, yarn_holder: 'none',
      question_active: true, question_x_q16: 32000, question_y_q16: 12000,
      collision_seq: 0,
    };
  }
  await advance(500);
  assert.equal(calls.state.length, staleWaitCount);
  // An action accepted during the wait is held, then applied on the restored world.
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'question_tap',
    intent_id: 60, x_q16: 44000, y_q16: 15000,
  });
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 30000);
  emit('neko-visit-cat-world', worldFrame(40, 47000, 'commit'));
  emit('neko-visit-cat-world', worldFrame(1, 10000));
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 44000);
  assert.ok(win.NekoVisitCatWorld.snapshot().published_world_seq > 40);
  await advance(1000);
  assert.ok(calls.state.slice(staleWaitCount).every((item) => item.world_seq > 40));
  // A later recovery commit with a newer cursor still replaces the world.
  emit('neko-visit-cat-world', worldFrame(500, 47000, 'commit'));
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 47000);
  assert.equal(win.NekoVisitCatWorld.snapshot().published_world_seq, 500);

  // Peer intent is automatically calculated by the host. No approval surface exists.
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'question_tap',
    intent_id: 7, x_q16: 42000, y_q16: 18000,
  });
  await flush();
  let snapshot = win.NekoVisitCatWorld.snapshot();
  assert.equal(snapshot.world.yarn_active, true);
  assert.equal(snapshot.world.yarn_x_q16, 42000);

  // Reliable local intents preserve grab-before-release order even when the
  // user performs a very fast drag.
  const yarn = scene.querySelector('[data-visit-cat-shared-object="yarn"]');

  // A physically cropped Pet window converts pointer input into the same
  // virtual-desktop coordinates used by native cat poses.
  win._getNekoDesktopVirtualViewportSize = () => ({ width: 2000, height: 1600 });
  let virtualRectCalls = 0;
  win._getNekoDesktopVirtualRect = (rect) => {
    virtualRectCalls += 1;
    return {
      left: Number(rect.left) + 500,
      top: Number(rect.top) + 300,
      width: Number(rect.width),
      height: Number(rect.height),
    };
  };
  win.__nekoNiriPetPhysicalCrop = {
    getEventCoordinates: (event) => ({
      local: { x: Number(event.clientX), y: Number(event.clientY) },
      virtual: { x: Number(event.clientX) + 500, y: Number(event.clientY) + 300 },
      patched: false,
    }),
  };
  const beforeCroppedDrag = calls.intent.length;
  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 3, clientX: 100, clientY: 200,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 3, clientX: 120, clientY: 220 });
  await flush();
  await flush();
  const croppedGrab = calls.intent[beforeCroppedDrag];
  assert.equal(croppedGrab.kind, 'yarn_grab');
  assert.equal(croppedGrab.x_q16, 19661);
  assert.equal(croppedGrab.y_q16, 20480);
  assert.equal(virtualRectCalls, 0);

  // An incomplete or throwing native bridge must still use the older generic
  // conversion instead of snapping the pointer to the virtual origin.
  win.__nekoNiriPetPhysicalCrop.getEventCoordinates = () => ({ virtual: null });
  const beforeIncompleteBridge = calls.intent.length;
  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 32, clientX: 100, clientY: 200,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 32, clientX: 120, clientY: 220 });
  await flush();
  await flush();
  assert.equal(calls.intent[beforeIncompleteBridge].x_q16, 19661);
  assert.equal(calls.intent[beforeIncompleteBridge].y_q16, 20480);
  const callsAfterIncompleteBridge = virtualRectCalls;
  assert.ok(callsAfterIncompleteBridge > 0);

  win.__nekoNiriPetPhysicalCrop.getEventCoordinates = () => ({ virtual: { x: null, y: 300 } });
  const beforePartialNativeBridge = calls.intent.length;
  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 34, clientX: 100, clientY: 200,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 34, clientX: 120, clientY: 220 });
  await flush();
  await flush();
  assert.equal(calls.intent[beforePartialNativeBridge].x_q16, 19661);
  assert.equal(calls.intent[beforePartialNativeBridge].y_q16, 20480);

  win.__nekoNiriPetPhysicalCrop.getEventCoordinates = () => { throw new Error('crop bridge unavailable'); };
  const beforeThrowingBridge = calls.intent.length;
  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 33, clientX: 100, clientY: 200,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 33, clientX: 120, clientY: 220 });
  await flush();
  await flush();
  assert.equal(calls.intent[beforeThrowingBridge].x_q16, 19661);
  assert.equal(calls.intent[beforeThrowingBridge].y_q16, 20480);
  assert.ok(virtualRectCalls > callsAfterIncompleteBridge);
  delete win.__nekoNiriPetPhysicalCrop;

  // Older desktop bridges expose only the generic local-to-virtual helpers.
  const beforeLegacyCroppedDrag = calls.intent.length;
  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 31, clientX: 100, clientY: 200,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 31, clientX: 120, clientY: 220 });
  await flush();
  await flush();
  const legacyCroppedGrab = calls.intent[beforeLegacyCroppedDrag];
  assert.equal(legacyCroppedGrab.x_q16, 19661);
  assert.equal(legacyCroppedGrab.y_q16, 20480);
  assert.ok(virtualRectCalls > 0);
  delete win._getNekoDesktopVirtualRect;

  // A partial/failed crop bridge must fall back as one unit instead of mixing
  // a virtual viewport with local pointer coordinates.
  const beforePartialBridge = calls.intent.length;
  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 30, clientX: 100, clientY: 200,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 30, clientX: 120, clientY: 220 });
  await flush();
  await flush();
  const fallbackGrab = calls.intent[beforePartialBridge];
  assert.equal(fallbackGrab.x_q16, 6554);
  assert.equal(fallbackGrab.y_q16, 16384);
  delete win._getNekoDesktopVirtualViewportSize;

  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 4, clientX: 640, clientY: 260,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 4, clientX: 650, clientY: 270 });
  await flush();
  assert.deepEqual(calls.intent.slice(-2).map((item) => item.kind), ['yarn_grab', 'yarn_release']);
  assert.ok(calls.intent[calls.intent.length - 1].intent_id > calls.intent[calls.intent.length - 2].intent_id);

  // A failed reliable intent is retried in place: the release still lands, and
  // never overtakes its grab, after the local drag has already ended.
  const beforeRetry = calls.intent.length;
  failIntents = 2;
  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 5, clientX: 640, clientY: 260,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 5, clientX: 650, clientY: 270 });
  await flush();
  await advance(2000);
  const retriedIntents = calls.intent.slice(beforeRetry).map((item) => item.kind);
  assert.deepEqual(retriedIntents, ['yarn_grab', 'yarn_grab', 'yarn_grab', 'yarn_release']);
  assert.equal(failIntents, 0);

  // The server accepted the grab but its response was lost: the retry comes
  // back stale at the same id, which keeps that id instead of renumbering.
  const beforeLost = calls.intent.length;
  loseIntentResponse = true;
  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 6, clientX: 640, clientY: 260,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 6, clientX: 650, clientY: 270 });
  await flush();
  await advance(2000);
  const lost = calls.intent.slice(beforeLost);
  assert.deepEqual(lost.map((item) => item.kind), ['yarn_grab', 'yarn_grab', 'yarn_release']);
  assert.equal(lost[0].intent_id, lost[1].intent_id);
  assert.ok(lost[2].intent_id > lost[1].intent_id);

  // A pointer is accepted only from the actor and intent that currently owns the yarn.
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'yarn_grab',
    intent_id: 8, x_q16: 41000, y_q16: 17000,
  });
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'self', kind: 'pointer',
    intent_id: 8, x_q16: 9000, y_q16: 9000,
  });
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 41000);
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'pointer',
    intent_id: 8, x_q16: 36000, y_q16: 16000,
  });
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 36000);
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'yarn_release',
    intent_id: 9, x_q16: 35000, y_q16: 16500,
  });
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_holder, 'none');

  // A resting yarn reaches a reliable commit and then stops publishing frames.
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'question_tap',
    intent_id: 10, x_q16: 33000, y_q16: 61000,
  });
  // Simultaneous gravity entry on both sides is idempotent at the host: the
  // second spawn cannot teleport or release an already visible yarn.
  const spawnStateCount = calls.state.length;
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'yarn_spawn',
    intent_id: 11, x_q16: 1000, y_q16: 2000,
  });
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 33000);
  assert.equal(calls.state.length, spawnStateCount);
  await flush();
  await advance(8000);
  const settledStateCount = calls.state.length;
  assert.equal(calls.state[calls.state.length - 1].phase, 'commit');
  await advance(1000);
  assert.equal(calls.state.length, settledStateCount);

  // The commit is the backend's recovery state: a failed one is retried with
  // backoff until it lands, then publishing stops again.
  failStates = 2;
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'question_tap',
    intent_id: 12, x_q16: 34000, y_q16: 61000,
  });
  await flush();
  await advance(8000);
  const retried = calls.state.slice(settledStateCount);
  assert.equal(failStates, 0);
  assert.ok(retried.filter((item) => item.phase === 'commit').length >= 3);
  assert.equal(retried[retried.length - 1].phase, 'commit');
  const retriedStateCount = calls.state.length;
  await advance(10000);
  assert.equal(calls.state.length, retriedStateCount);

  // A reload during a grab restores a holder with no drag authority behind it:
  // the host drops the yarn instead of leaving it locked for both cats.
  emit('neko-visit-cat-world', Object.assign(worldFrame(900, 30000, 'commit'), { yarn_holder: 'guest' }));
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_holder, 'none');
  await flush();
  assert.equal(calls.state[calls.state.length - 1].phase, 'commit');
  assert.equal(calls.state[calls.state.length - 1].yarn_holder, 'none');
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'self', kind: 'yarn_grab',
    intent_id: 13, x_q16: 30000, y_q16: 20000,
  });
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_holder, 'host');
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'self', kind: 'yarn_release',
    intent_id: 14, x_q16: 30000, y_q16: 20000,
  });
  await flush();
  await advance(8000);

  // A cat standing exactly on the yarn still pushes it out instead of trapping it.
  const resting = win.NekoVisitCatWorld.snapshot().world;
  emit('neko-visit-cat-pose', {
    type: 'visit_cat_pose', visit_id: VID, actor: 'peer', phase: 'sample',
    motion_id: 9000, sample_seq: 1, x_q16: resting.yarn_x_q16, y_q16: resting.yarn_y_q16,
  });
  await advance(200);
  const pushed = win.NekoVisitCatWorld.snapshot().world;
  assert.ok(pushed.collision_seq > resting.collision_seq);
  assert.notEqual(pushed.yarn_y_q16, resting.yarn_y_q16);
  await advance(8000);

  // Ending immediately stops host calculation; it cannot start again behind
  // the exit confirmation flow.
  lifecycle('ending', 'host');
  assert.equal(win.NekoVisitCatWorld.snapshot().simulation_started, false);
  const endingStateCount = calls.state.length;
  await advance(1000);
  assert.equal(calls.state.length, endingStateCount);

  // State publication is a fixed normalized schema, never a pixel/window/resource payload.
  await flush();
  const allowed = [
    'collision_seq', 'phase', 'question_active', 'question_x_q16', 'question_y_q16',
    'visit_id', 'world_seq', 'yarn_active', 'yarn_holder', 'yarn_vx_q12',
    'yarn_vy_q12', 'yarn_x_q16', 'yarn_y_q16',
  ];
  calls.state.forEach((payload) => assert.deepEqual(Object.keys(payload).sort(), allowed.slice().sort()));

  // Guests accept only increasing authoritative world_seq values.
  lifecycle('ended', 'host');
  lifecycle('started', 'guest');
  emit('neko-visit-cat-world', worldFrame(5, 50000));
  emit('neko-visit-cat-world', worldFrame(4, 10000));
  snapshot = win.NekoVisitCatWorld.snapshot();
  assert.equal(snapshot.last_applied_world_seq, 5);
  assert.equal(snapshot.world.yarn_x_q16, 50000);

  // A guest reload (or a release lost on the way to the host page) leaves the
  // world showing this side holding the yarn with nothing dragging it here.
  const beforeHeld = calls.intent.length;
  emit('neko-visit-cat-world', Object.assign(worldFrame(6, 50000), { yarn_holder: 'guest' }));
  await advance(1000);
  assert.equal(calls.intent.length, beforeHeld);
  await advance(600);
  await flush();
  assert.deepEqual(calls.intent.slice(beforeHeld).map((item) => item.kind), ['yarn_release']);
  emit('neko-visit-cat-world', worldFrame(7, 50000));
  await advance(20000);
  await flush();
  assert.equal(calls.intent.length, beforeHeld + 1);

  // A page that stops receiving the world (its display taken over by another
  // window) gives up after a few releases instead of releasing forever.
  const beforeStale = calls.intent.length;
  emit('neko-visit-cat-world', Object.assign(worldFrame(8, 50000), { yarn_holder: 'guest' }));
  await advance(60000);
  await flush();
  assert.deepEqual(calls.intent.slice(beforeStale).map((item) => item.kind),
    ['yarn_release', 'yarn_release', 'yarn_release']);
  emit('neko-visit-cat-world', worldFrame(9, 50000));

  // While one release is still being retried, no second release queues behind it.
  failIntents = 1000;
  const beforeBusy = calls.intent.length;
  // The host keeps reporting the yarn as held while the release is retried.
  for (let seq = 10; seq < 16; seq += 1) {
    emit('neko-visit-cat-world', Object.assign(worldFrame(seq, 50000), { yarn_holder: 'guest' }));
    await advance(5000);
    await flush();
  }
  const busy = calls.intent.slice(beforeBusy);
  assert.ok(busy.length > 3);
  assert.equal(new Set(busy.map((item) => item.intent_id)).size, 1);
  failIntents = 0;
  emit('neko-visit-cat-world', worldFrame(16, 50000));
  await advance(5000);
  await flush();
  // Nothing piled up behind it: once the API is back only that one release went out.
  assert.equal(new Set(calls.intent.slice(beforeBusy).map((item) => item.intent_id)).size, 1);

  // A refused attempt never landed: a stale reply at its own id is renumbered,
  // not taken as acceptance.
  const beforeRefused = calls.intent.length;
  refuseThenStaleSame = true;
  scene.querySelector('[data-visit-cat-shared-object="question"]').click();
  await flush();
  await advance(2000);
  const refused = calls.intent.slice(beforeRefused);
  assert.deepEqual(refused.map((item) => item.kind), ['question_tap', 'question_tap', 'question_tap']);
  assert.equal(refused[0].intent_id, refused[1].intent_id);
  assert.ok(refused[2].intent_id > refused[1].intent_id);
  const storageKey = 'nekoVisitCatWorldIntentSequence:' + VID;
  assert.equal(Number(storage.get(storageKey)), refused[2].intent_id);

  // Leaving removes shared objects and cancels all future computation.
  const statesBeforeExit = calls.state.length;
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });
  assert.equal(win.NekoVisitCatWorld.isActive(), false);
  assert.equal(scene.querySelector('[data-visit-cat-shared-object="yarn"]'), null);
  await advance(1000);
  assert.equal(calls.state.length, statesBeforeExit);

  // A reloaded page of the same visit continues after the ids it already used.
  storage.set(storageKey, '5000');
  lifecycle('started', 'guest');
  emit('neko-visit-cat-world', worldFrame(8, 40000));
  const beforeReload = calls.intent.length;
  scene.querySelector('[data-visit-cat-shared-object="question"]').click();
  await flush();
  assert.equal(calls.intent[beforeReload].intent_id, 5001);
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });

  // A visit that ends while an automatic release is in flight does not block
  // automatic releases in the next visit on this page.
  lifecycle('started', 'guest');
  failIntents = 1000;
  emit('neko-visit-cat-world', Object.assign(worldFrame(1, 50000), { yarn_holder: 'guest' }));
  await advance(2000);
  await flush();
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });
  await advance(5000);
  await flush();
  failIntents = 0;
  lifecycle('started', 'guest');
  const beforeNextVisit = calls.intent.length;
  emit('neko-visit-cat-world', Object.assign(worldFrame(1, 50000), { yarn_holder: 'guest' }));
  await advance(2000);
  await flush();
  assert.ok(calls.intent.slice(beforeNextVisit).some((item) => item.kind === 'yarn_release'));
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });

  // Host reload whose saved world arrives only after the wait gave up, and
  // below the runtime cursor (a later frame moved it): it still wins.
  lifecycle('departed', 'host');
  staleStateCursor = 300;
  await advance(181);
  await flush();
  const lateBefore = calls.state.length;
  // Another publisher moved the cursor meanwhile: the resend's stale reply extends it.
  staleStateCursor = 320;
  await advance(1100);
  await flush();
  assert.ok(calls.state.slice(lateBefore).some((item) => item.phase === 'commit' && item.world_seq === 321));
  // A transient frame at or below the cursor is not the saved world.
  emit('neko-visit-cat-world', worldFrame(310, 9000));
  assert.notEqual(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 9000);
  // An action applied after the wait gave up is replayed on the late saved world.
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'question_tap',
    intent_id: 80, x_q16: 33000, y_q16: 15000,
  });
  // A slow drag made meanwhile keeps its real timing when replayed.
  const peerIntent = (kind, intentId, x) => emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind,
    intent_id: intentId, x_q16: x, y_q16: 15000,
  });
  peerIntent('yarn_grab', 81, 33000);
  for (let step = 1; step <= 6; step += 1) {
    await advance(100);
    peerIntent('pointer', 81, 33000 + step * 160);
  }
  peerIntent('yarn_release', 82, 34000);
  emit('neko-visit-cat-world', Object.assign(worldFrame(305, 12345, 'commit'), { question_x_q16: 777 }));
  const restored = win.NekoVisitCatWorld.snapshot().world;
  assert.equal(restored.question_x_q16, 777);
  assert.equal(restored.yarn_x_q16, 34000);
  assert.equal(restored.yarn_holder, 'none');
  assert.ok(restored.yarn_vx_q12 > 0 && restored.yarn_vx_q12 < 1000, String(restored.yarn_vx_q12));
  await flush();
  const republished = calls.state.filter((item) => item.phase === 'commit').pop();
  assert.equal(republished.question_x_q16, 777);
  assert.ok(republished.world_seq > 321);

  // A stale commit that is retried resends the world as it is now.
  let releaseGate;
  stateGate = new Promise((resolve) => { releaseGate = resolve; });
  staleStateCursor = 1;
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'question_tap',
    intent_id: 70, x_q16: 11000, y_q16: 15000,
  });
  await flush();
  const gatedBefore = calls.state.length;
  emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor: 'peer', kind: 'question_tap',
    intent_id: 71, x_q16: 22000, y_q16: 15000,
  });
  releaseGate();
  stateGate = null;
  await flush();
  assert.equal(calls.state[gatedBefore].yarn_x_q16, 22000);
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });

  // A bfcache restore does not rerun this script. Rebind once and replay the
  // relay's current snapshots so later authoritative frames remain visible.
  win.nekoVisitDisplayStateSnapshot = () => ({
    type: 'visit_state_change', action: 'departed', visit_id: VID, side: 'guest', cat_scene: 2,
  });
  win.nekoVisitCatPoseSnapshots = () => [];
  win.nekoVisitCatWorldSnapshot = () => worldFrame(800, 48000, 'commit');
  win.nekoVisitCatWorldIntentSnapshots = () => [];
  win.dispatchEvent({ type: 'pagehide', persisted: true });
  assert.equal(win.NekoVisitCatWorld.isActive(), false);
  assert.equal((listeners.get('neko-visit-cat-world') || []).length, 0);
  win.dispatchEvent({ type: 'pageshow', persisted: true });
  assert.equal(win.NekoVisitCatWorld.isActive(), true);
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 48000);
  assert.equal((listeners.get('neko-visit-cat-world') || []).length, 1);
  win.dispatchEvent({ type: 'pagehide', persisted: true });
  win.dispatchEvent({ type: 'pageshow', persisted: true });
  assert.equal(win.NekoVisitCatWorld.isActive(), true);
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 48000);
  assert.equal((listeners.get('neko-visit-cat-world') || []).length, 1);
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });

  // A host restored from BFCache restarts its authoritative simulator and
  // publishes a cursor newer than the retained commit.
  win.nekoVisitDisplayStateSnapshot = () => ({
    type: 'visit_state_change', action: 'departed', visit_id: VID, side: 'host', cat_scene: 2,
  });
  let retainedHostWorld = worldFrame(900, 49000, 'commit');
  win.nekoVisitCatWorldSnapshot = () => retainedHostWorld;
  win.dispatchEvent({ type: 'pagehide', persisted: true });
  win.dispatchEvent({ type: 'pageshow', persisted: true });
  await advance(181);
  await flush();
  const firstRestoredHostCursor = win.NekoVisitCatWorld.snapshot().published_world_seq;
  assert.ok(firstRestoredHostCursor > 900);
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 49000);
  retainedHostWorld = worldFrame(firstRestoredHostCursor, 49000, 'commit');
  const statesBeforeHostRestore = calls.state.length;
  win.dispatchEvent({ type: 'pagehide', persisted: true });
  win.dispatchEvent({ type: 'pageshow', persisted: true });
  await advance(181);
  await flush();
  assert.ok(win.NekoVisitCatWorld.snapshot().published_world_seq > firstRestoredHostCursor);
  assert.ok(calls.state.length > statesBeforeHostRestore);
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });

  // A recovery that replaces a world with a drag in progress drops that drag,
  // so a saved holder from that world is released instead of locking the yarn.
  const hostIntent = (actor, kind, intentId, x) => emit('neko-visit-cat-world-intent', {
    type: 'visit_cat_world_intent', visit_id: VID, actor, kind,
    intent_id: intentId, x_q16: x, y_q16: 15000,
  });
  lifecycle('departed', 'host');
  await advance(181);
  await flush();
  hostIntent('self', 'question_tap', 100, 20000);
  hostIntent('self', 'yarn_grab', 101, 20000);
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_holder, 'host');
  await flush();
  staleStateCursor = 600;
  hostIntent('self', 'pointer', 101, 21000);
  await flush();
  emit('neko-visit-cat-world', Object.assign(worldFrame(590, 25000, 'commit'), { yarn_holder: 'guest' }));
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_holder, 'none');
  hostIntent('peer', 'yarn_grab', 102, 25000);
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_holder, 'guest');
  // A clock stepped back mid-drag drops the yarn instead of flinging it.
  hostIntent('peer', 'pointer', 102, 26000);
  now -= 5000;
  hostIntent('peer', 'yarn_release', 103, 27000);
  now += 5000;
  const dropped = win.NekoVisitCatWorld.snapshot().world;
  assert.equal(dropped.yarn_holder, 'none');
  assert.equal(dropped.yarn_vx_q12, 0);
  assert.equal(dropped.yarn_vy_q12, 0);
  // Also when the clock steps back and recovers before the release.
  hostIntent('peer', 'yarn_grab', 104, 27000);
  now -= 5000;
  hostIntent('peer', 'pointer', 104, 29000);
  now += 5000;
  hostIntent('peer', 'yarn_release', 105, 31000);
  const recovered = win.NekoVisitCatWorld.snapshot().world;
  assert.equal(recovered.yarn_holder, 'none');
  assert.equal(recovered.yarn_vx_q12, 0);
  assert.equal(recovered.yarn_vy_q12, 0);
  // Also when the samples from before the step were already trimmed away.
  hostIntent('peer', 'yarn_grab', 106, 31000);
  now -= 5000;
  for (let step = 1; step <= 6; step += 1) {
    now += 20;
    hostIntent('peer', 'pointer', 106, 31000 + step * 800);
  }
  hostIntent('peer', 'yarn_release', 107, 36600);
  now += 5000;
  const trimmed = win.NekoVisitCatWorld.snapshot().world;
  assert.equal(trimmed.yarn_holder, 'none');
  assert.equal(trimmed.yarn_vx_q12, 0);
  assert.equal(trimmed.yarn_vy_q12, 0);
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });

  // A replay that grows too long gives up on the late saved world rather than
  // replaying a sequence with its oldest actions dropped.
  lifecycle('departed', 'host');
  staleStateCursor = 700;
  await advance(181);
  await flush();
  await advance(1100);
  await flush();
  for (let index = 0; index < 260; index += 1) hostIntent('peer', 'pointer', 110, 30000);
  emit('neko-visit-cat-world', Object.assign(worldFrame(690, 31000, 'commit'), { question_x_q16: 555 }));
  assert.notEqual(win.NekoVisitCatWorld.snapshot().world.question_x_q16, 555);
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });

  process.stdout.write(JSON.stringify({
    guestIntentCount: calls.intent.length,
    hostStateCount: statesBeforeExit,
    geometryEvents: geometry.length,
    finalActive: win.NekoVisitCatWorld.isActive(),
  }));
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_cat_visit_shared_world_is_host_authoritative_and_bounded() -> None:
    node = shutil.which("node")
    assert node is not None
    completed = run_node_script(
        node,
        NODE_SCENARIO.replace("__WORLD_PATH__", json.dumps(str(WORLD_PATH))),
        timeout=20,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["guestIntentCount"] >= 21
    assert result["hostStateCount"] >= 3
    assert result["geometryEvents"] >= 3
    assert result["finalActive"] is False


def test_active_cat_visit_reuses_playground_entry_without_starting_solo_world() -> None:
    source = PLAYGROUND_PATH.read_text(encoding="utf-8")
    block = source.split(
        "function _handleNekoIdleCat1PlaygroundEntryRequest(event)", 1
    )[1].split("if (typeof window !== 'undefined')", 1)[0]
    assert "window.nekoVisitCatScene" in block
    assert "visitCatScene.isActive()" in block
    assert block.index("visitCatScene.isActive()") < block.index(
        "_getNekoIdleCat1PlaygroundEntryButton()"
    )
