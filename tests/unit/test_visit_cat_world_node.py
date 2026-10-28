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
        return Promise.resolve(stateResponses === 1
          ? { stale: true, current_world_seq: 40 }
          : { accepted: true });
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

  // Ordinary cat visits do not create a question block.  A newer peer may
  // still explicitly publish the legacy field, which remains wire-compatible.
  emit('neko-visit-cat-world', {
    type: 'visit_cat_world', visit_id: VID, phase: 'commit', world_seq: 1,
    yarn_active: false, yarn_x_q16: 32768, yarn_y_q16: 19661,
    yarn_vx_q12: 0, yarn_vy_q12: 0, yarn_holder: 'none',
    question_active: true, question_x_q16: 32768, question_y_q16: 13107,
    collision_seq: 0,
  });
  assert.equal(question.hidden, false);
  staleIntentOnce = true;
  question.click();
  await advance(400);
  assert.equal(calls.intent.length, 2);
  assert.equal(calls.intent[0].kind, 'question_tap');
  assert.equal(calls.intent[1].intent_id, 41);
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
  assert.ok(win.NekoVisitCatWorld.snapshot().published_world_seq >= 41);

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
  emit('neko-visit-cat-world', worldFrame(50, 47000, 'commit'));
  emit('neko-visit-cat-world', worldFrame(1, 10000));
  assert.equal(win.NekoVisitCatWorld.snapshot().world.yarn_x_q16, 47000);
  assert.equal(win.NekoVisitCatWorld.snapshot().published_world_seq, 50);

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
  yarn.dispatchEvent({ type: 'pointerdown', button: 0, pointerId: 4, clientX: 640, clientY: 260,
    preventDefault() {}, stopPropagation() {} });
  yarn.dispatchEvent({ type: 'pointerup', button: 0, pointerId: 4, clientX: 650, clientY: 270 });
  await flush();
  assert.deepEqual(calls.intent.slice(-2).map((item) => item.kind), ['yarn_grab', 'yarn_release']);
  assert.ok(calls.intent[calls.intent.length - 1].intent_id > calls.intent[calls.intent.length - 2].intent_id);

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
  await advance(8000);
  const settledStateCount = calls.state.length;
  assert.equal(calls.state[calls.state.length - 1].phase, 'commit');
  await advance(1000);
  assert.equal(calls.state.length, settledStateCount);

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

  // Leaving removes shared objects and cancels all future computation.
  const statesBeforeExit = calls.state.length;
  emit('neko:visit-cat-scene-deactivated', { visit_id: VID });
  assert.equal(win.NekoVisitCatWorld.isActive(), false);
  assert.equal(scene.querySelector('[data-visit-cat-shared-object="yarn"]'), null);
  await advance(1000);
  assert.equal(calls.state.length, statesBeforeExit);

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
    assert result["guestIntentCount"] == 4
    assert result["hostStateCount"] >= 3
    assert result["geometryEvents"] >= 3
    assert result["finalActive"] is False
