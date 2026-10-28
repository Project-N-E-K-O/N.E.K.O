# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Browser contract for the v2 cat-form visit scene.

The production scene runs in a small Node DOM with a deterministic animation
clock.  These scenarios pin the stage-two boundary: the existing local cat is
reused, owner pose samples are transported, the peer is a read-only semantic
mirror, stale motion cannot rewind it, and returning to the model exits first.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENE_PATH = PROJECT_ROOT / "static" / "visit" / "cat-scene.js"

HARNESS = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const SRC = fs.readFileSync(__SCENE_PATH__, 'utf8');
const VID = 'AAAAAAAAAAAAAAAAAAAAAA';

class CustomEventLike {
  constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
}

class ClassList {
  constructor(node) { this.node = node; this.values = new Set(); }
  add(...names) { names.forEach((name) => this.values.add(String(name))); this.sync(); }
  remove(...names) { names.forEach((name) => this.values.delete(String(name))); this.sync(); }
  contains(name) { return this.values.has(String(name)); }
  toggle(name, force) {
    const enabled = force === undefined ? !this.contains(name) : !!force;
    if (enabled) this.values.add(String(name)); else this.values.delete(String(name));
    this.sync();
    return enabled;
  }
  setFromString(value) {
    this.values = new Set(String(value || '').split(' ').filter(Boolean));
    this.sync();
  }
  sync() { this.node._className = Array.from(this.values).join(' '); }
}

class StyleLike {
  constructor() { this.values = Object.create(null); }
  setProperty(name, value) {
    this.values[String(name)] = String(value);
    this[String(name)] = String(value);
  }
  removeProperty(name) {
    const key = String(name);
    delete this.values[key];
    delete this[key];
  }
  getPropertyValue(name) { return this.values[String(name)] || ''; }
}

function attrNameForDataset(key) {
  return 'data-' + String(key).replace(/[A-Z]/g, (match) => '-' + match.toLowerCase());
}

function matches(node, selector) {
  selector = String(selector || '').trim();
  if (!selector) return false;
  if (selector[0] === '#') return node.id === selector.slice(1);
  if (selector[0] === '.') return node.classList.contains(selector.slice(1));
  if (selector.startsWith('[') && selector.endsWith(']')) {
    const content = selector.slice(1, -1);
    const suffixIndex = content.indexOf('$=');
    const equalIndex = suffixIndex < 0 ? content.indexOf('=') : -1;
    const operatorIndex = suffixIndex >= 0 ? suffixIndex : equalIndex;
    if (operatorIndex < 0) return node.getAttribute(content) !== null;
    const operator = suffixIndex >= 0 ? '$=' : '=';
    const name = content.slice(0, operatorIndex);
    let expected = content.slice(operatorIndex + operator.length);
    if ((expected.startsWith('"') && expected.endsWith('"'))
        || (expected.startsWith("'") && expected.endsWith("'"))) {
      expected = expected.slice(1, -1);
    }
    const value = node.getAttribute(name);
    if (operator === '=') return value === expected;
    if (operator === '$=') return value !== null && value.endsWith(expected);
  }
  return node.tagName === selector.toUpperCase();
}

class NodeLike {
  constructor(tag) {
    this.tagName = String(tag || '').toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attrs = {};
    this.listeners = {};
    this.style = new StyleLike();
    this.dataset = new Proxy({}, {
      get: (_target, key) => this.getAttribute(attrNameForDataset(key)),
      set: (_target, key, value) => {
        this.setAttribute(attrNameForDataset(key), value);
        return true;
      },
    });
    this.classList = new ClassList(this);
    this._className = '';
    this._text = '';
    this._rect = { left: 0, top: 0, width: 100, height: 100 };
    this.hidden = false;
    this.disabled = false;
    this.src = '';
    this.alt = '';
    this.id = '';
    this.isConnected = false;
  }
  get className() { return this._className; }
  set className(value) {
    this._className = String(value || '');
    this.classList.setFromString(this._className);
  }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
  set textContent(value) {
    this.children = [];
    this._text = String(value || '');
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) {
    child.parentNode = this;
    child.isConnected = this.isConnected;
    this.children.push(child);
    child.walk((node) => { node.isConnected = child.isConnected; });
    return child;
  }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    child.parentNode = null;
    child.walk((node) => { node.isConnected = false; });
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === 'id') this.id = String(value);
    if (name === 'class') this.className = String(value);
    if (name === 'hidden') this.hidden = true;
    if (name === 'src') this.src = String(value);
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }
  removeAttribute(name) {
    delete this.attrs[name];
    if (name === 'hidden') this.hidden = false;
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    const list = this.listeners[type] || [];
    const index = list.indexOf(fn);
    if (index >= 0) list.splice(index, 1);
  }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    (this.listeners[event.type] || []).slice().forEach((fn) => fn(event));
    return true;
  }
  click() {
    if (this.disabled) return;
    this.dispatchEvent({ type: 'click', target: this, preventDefault() {}, stopPropagation() {} });
  }
  querySelector(selector) {
    return this.all((node) => node !== this && matches(node, selector))[0] || null;
  }
  querySelectorAll(selector) {
    return this.all((node) => node !== this && matches(node, selector));
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (matches(node, selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  getBoundingClientRect() {
    return {
      left: this._rect.left,
      top: this._rect.top,
      width: this._rect.width,
      height: this._rect.height,
      right: this._rect.left + this._rect.width,
      bottom: this._rect.top + this._rect.height,
    };
  }
  walk(fn) {
    fn(this);
    this.children.forEach((child) => child.walk(fn));
  }
  all(predicate) {
    const output = [];
    this.walk((node) => { if (predicate(node)) output.push(node); });
    return output;
  }
  focus() {}
}

function createHarness(options) {
  options = options || {};
  const listeners = new Map();
  const tasks = [];
  let nextTask = 1;
  let now = 0;
  const poseCalls = [];
  const endCalls = [];
  const confirms = [];
  const confirmAnswers = [];
  let geometryEvents = 0;
  let entryEvents = 0;
  let localSnapshot = null;
  let poseHandler = null;

  const body = new NodeLike('body');
  body.isConnected = true;
  const documentShim = {
    body,
    documentElement: body,
    createElement: (tag) => new NodeLike(tag),
    getElementById(id) { return body.all((node) => node.id === id)[0] || null; },
    querySelector(selector) { return body.querySelector(selector); },
    querySelectorAll(selector) { return body.querySelectorAll(selector); },
  };

  function schedule(kind, fn, delay) {
    const task = { id: nextTask++, kind, at: now + Math.max(0, Number(delay) || 0), fn };
    tasks.push(task);
    return task.id;
  }
  function cancel(id) {
    const index = tasks.findIndex((task) => task.id === id);
    if (index >= 0) tasks.splice(index, 1);
  }

  const win = {
    document: documentShim,
    innerWidth: 1000,
    innerHeight: 800,
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const list = listeners.get(type) || [];
      const index = list.indexOf(fn);
      if (index >= 0) list.splice(index, 1);
    },
    dispatchEvent(event) {
      if (event.type === 'neko:visit-cat-geometry-changed') geometryEvents += 1;
      if (event.type === 'live2d-goodbye-click') entryEvents += 1;
      if (event.type === 'neko:goodbye-idle-appearance') {
        const mode = event.detail && event.detail.mode;
        win.__nekoGoodbyeIdleAppearance = mode;
        documentShim.querySelectorAll('.neko-idle-return-btn').forEach((button) => {
          button.setAttribute('data-neko-idle-tier', mode === 'ball' ? 'none' : 'cat1');
        });
      }
      (listeners.get(event.type) || []).slice().forEach((fn) => fn(event));
      return true;
    },
    requestAnimationFrame(fn) { return schedule('raf', fn, 16); },
    cancelAnimationFrame(id) { cancel(id); },
    NekoCatResourceRegistry: {
      getAppearanceForGroup(groupId, slot, options) {
        const seed = Number(options && options.seed) || 0;
        return {
          available: true,
          groupId,
          url: `/cat/${groupId}/${slot}.gif?seed=${seed}`,
        };
      },
    },
    NekoIdleCatVisitBridge: {
      snapshot() { return localSnapshot && Object.assign({}, localSnapshot); },
    },
    nekoVisitChat: {
      async requestCatPose(visitId, phase, pose) {
        poseCalls.push({ visitId, phase, pose: Object.assign({}, pose) });
        if (poseHandler) return poseHandler(visitId, phase, pose);
        return { ok: true };
      },
      async endCatVisitForModelReturn(visitId) {
        endCalls.push(visitId);
        return true;
      },
    },
    async showConfirm(message, title, options) {
      confirms.push({ message, title, options });
      return confirmAnswers.length ? confirmAnswers.shift() : false;
    },
    nekoVisitDisplayStateSnapshot: options.displaySnapshot
      ? () => Object.assign({}, options.displaySnapshot) : undefined,
    nekoVisitCatPoseSnapshots: options.poseSnapshots
      ? () => options.poseSnapshots.map((pose) => Object.assign({}, pose)) : undefined,
  };
  win.window = win;

  const sandbox = {
    window: win,
    document: documentShim,
    console,
    CustomEvent: CustomEventLike,
    Date: { now: () => 1700000000000 + now },
    performance: { now: () => now },
    setTimeout(fn, ms) { return schedule('timer', fn, ms); },
    clearTimeout(id) { cancel(id); },
    Map, Set, Number, Math, JSON, Object, Array, String, Promise, RegExp, Error,
  };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'cat-scene.js' });

  function emit(type, detail) { win.dispatchEvent(new CustomEventLike(type, { detail })); }
  function state(action, extra) {
    emit('neko-visit-display-state', Object.assign({
      type: 'visit_state_change',
      action,
      visit_id: VID,
      side: 'host',
    }, extra || {}));
  }
  function stateFor(visitId, action, extra) {
    emit('neko-visit-display-state', Object.assign({
      type: 'visit_state_change',
      action,
      visit_id: visitId,
      side: 'host',
    }, extra || {}));
  }
  function pose(actor, phase, motionId, sampleSeq, extra) {
    emit('neko-visit-cat-pose', Object.assign({
      type: 'visit_cat_pose',
      visit_id: VID,
      actor,
      phase,
      motion_id: motionId,
      sample_seq: sampleSeq,
      x_q16: 16384,
      y_q16: 32768,
      mode: 'walk',
      facing: 'right',
      tier: 'cat1',
      slot: 'movement.cat1.walking',
    }, extra || {}));
  }
  function advance(ms) {
    const target = now + ms;
    for (;;) {
      tasks.sort((left, right) => left.at - right.at || left.id - right.id);
      const task = tasks[0];
      if (!task || task.at > target) break;
      tasks.shift();
      now = task.at;
      task.fn(now);
    }
    now = target;
  }
  async function settle() {
    for (let index = 0; index < 12; index += 1) {
      await new Promise((resolve) => setImmediate(resolve));
    }
  }
  function root() { return documentShim.getElementById('neko-visit-cat-scene'); }
  function card(actor) {
    const scene = root();
    return scene && scene.all((node) =>
      node.classList.contains('neko-visit-cat-card')
      && node.getAttribute('data-visit-cat-actor') === actor)[0] || null;
  }
  function art(actor) {
    const actorCard = card(actor);
    return actorCard && actorCard.querySelector('[data-visit-cat-art]');
  }
  function visible(node) {
    let current = node;
    while (current) {
      if (current.hidden || current.style.display === 'none') return false;
      current = current.parentNode;
    }
    return !!node;
  }
  function visibleOwnerLabels(text) {
    return body.all((node) =>
      (node.classList.contains('neko-visit-cat-owner')
        || node.classList.contains('neko-visit-cat-native-label'))
      && node.textContent === text
      && visible(node));
  }
  function ownerLabel(actor) {
    return body.all((node) => node.getAttribute
      && node.getAttribute('data-visit-cat-owner-label') === actor)[0] || null;
  }
  function installNativeSelf() {
    const container = new NodeLike('div');
    container.id = 'live2d-return-button-container';
    container.setAttribute('id', container.id);
    container.style.display = 'block';
    container._rect = { left: 200, top: 240, width: 112, height: 112 };
    const button = new NodeLike('div');
    button.className = 'neko-idle-return-btn';
    button.setAttribute('data-neko-idle-tier', 'cat1');
    const image = new NodeLike('img');
    image.className = 'neko-idle-return-art';
    image.setAttribute('src', '/cat/dev_neko/idle.cat1.gif');
    button.appendChild(image);
    container.appendChild(button);
    body.appendChild(container);
    return { container, button, image };
  }
  function setSnapshot(value) { localSnapshot = value && Object.assign({}, value); }
  function pendingTasks() { return tasks.length; }

  return {
    win,
    body,
    poseCalls,
    endCalls,
    confirms,
    confirmAnswers,
    emit,
    state,
    stateFor,
    pose,
    advance,
    settle,
    root,
    card,
    art,
    visible,
    visibleOwnerLabels,
    ownerLabel,
    installNativeSelf,
    setSnapshot,
    setPoseHandler(handler) { poseHandler = handler; },
    pendingTasks,
    geometryCount: () => geometryEvents,
    entryCount: () => entryEvents,
  };
}

const report = {};

(async () => {
  const h = createHarness();

  h.state('started', {
    cat_scene: 1,
    self_cat_asset: 'dev_neko',
    peer_cat_asset: 'yui_neko',
  });
  report.v1_ignored = h.root() === null;

  h.state('started', {
    cat_scene: 2,
    self_cat_asset: 'dev_neko',
    peer_cat_asset: 'yui_neko',
  });
  report.root_created = !!h.root();
  report.body_active = h.body.classList.contains('neko-visit-cat-scene-active');
  report.entry_events = h.entryCount();
  report.no_action_buttons = h.root().querySelectorAll('[data-visit-cat-action]').length === 0;

  const native = h.installNativeSelf();
  h.setSnapshot({
    tier: 'cat1',
    slot: 'idle.cat1',
    mode: 'idle',
    facing: 'left',
    x_q16: 16777,
    y_q16: 24248,
  });
  h.advance(128);
  await h.settle();
  native.container._rect.top = 0;
  h.advance(16);
  const nativeLabelAtTop = h.visibleOwnerLabels('我的猫')[0];
  report.native = {
    bound_actor: native.container.getAttribute('data-visit-cat-actor'),
    input_role: native.container.getAttribute('data-neko-visit-cat-input'),
    fallback_hidden: h.card('self').hidden,
    my_labels: h.visibleOwnerLabels('我的猫').length,
    sample_calls: h.poseCalls.filter((call) => call.phase === 'sample').length,
    top_label_y: Number.parseFloat(nativeLabelAtTop.style.top),
  };
  native.container._rect.top = 240;

  h.pose('peer', 'sample', 4, 1);
  const peer = h.card('peer');
  report.peer_sample = {
    visible: h.visible(peer),
    left: Number.parseFloat(peer.style.getPropertyValue('--neko-visit-cat-x')),
    top: Number.parseFloat(peer.style.getPropertyValue('--neko-visit-cat-y')),
    facing: peer.getAttribute('data-visit-cat-facing'),
    mode: peer.getAttribute('data-visit-cat-mode'),
    asset: peer.getAttribute('data-visit-cat-asset'),
    src: h.art('peer').src,
    top_label_y: Number.parseFloat(h.ownerLabel('peer').style.top),
  };

  h.pose('peer', 'commit', 4, 2, {
    x_q16: 22000,
    y_q16: 36000,
    mode: 'idle',
    facing: 'left',
    tier: 'cat2',
    slot: 'idle.cat2',
  });
  const committedLeft = peer.style.getPropertyValue('--neko-visit-cat-x');
  const committedSrc = h.art('peer').src;
  h.pose('peer', 'sample', 4, 3, {
    x_q16: 50000,
    y_q16: 50000,
    mode: 'drag',
    facing: 'right',
    tier: 'cat2',
    slot: 'drag.cat2',
  });
  report.late_sample = {
    ignored_position: peer.style.getPropertyValue('--neko-visit-cat-x') === committedLeft,
    ignored_art: h.art('peer').src === committedSrc,
  };
  h.pose('peer', 'sample', 5, 1, {
    x_q16: 50000,
    y_q16: 50000,
    mode: 'drag',
    facing: 'right',
    tier: 'cat2',
    slot: 'drag.cat2',
  });
  report.new_motion = {
    accepted: peer.style.getPropertyValue('--neko-visit-cat-x') !== committedLeft,
    tier: peer.getAttribute('data-visit-cat-tier'),
    src: h.art('peer').src,
  };
  h.pose('peer', 'sample', 6, 1, {
    slot: 'action.cat1.play_yarn',
    mode: 'idle',
  });
  report.wide_slot = peer.getAttribute('data-visit-cat-action-state');

  h.advance(500);
  await h.settle();
  report.pose_transport = h.poseCalls.map((call) => ({
    visitId: call.visitId,
    phase: call.phase,
    motion_id: call.pose.motion_id,
    sample_seq: call.pose.sample_seq,
    keys: Object.keys(call.pose).sort(),
  }));
  report.geometry_events = h.geometryCount();

  let returnCommits = 0;
  h.confirmAnswers.push(false);
  report.cancel_guarded = h.win.nekoVisitCatScene.guardModelReturn(() => { returnCommits += 1; });
  await h.settle();
  report.after_cancel = {
    commits: returnCommits,
    endCalls: h.endCalls.length,
    active: h.body.classList.contains('neko-visit-cat-scene-active'),
  };

  h.confirmAnswers.push(true);
  report.confirm_guarded = h.win.nekoVisitCatScene.guardModelReturn(() => { returnCommits += 1; });
  await h.settle();
  report.after_confirm = {
    commits: returnCommits,
    endCalls: h.endCalls.slice(),
    active: h.body.classList.contains('neko-visit-cat-scene-active'),
  };
  report.request_model_return_exposed =
    typeof h.win.nekoVisitCatScene.requestModelReturn === 'function';

  let nativeReturnClicks = 0;
  native.button.addEventListener('click', () => { nativeReturnClicks += 1; });
  h.win.__nekoGoodbyeIdleAppearance = 'ball';
  h.state('started', {
    cat_scene: 2,
    self_cat_asset: 'dev_neko',
    peer_cat_asset: 'yui_neko',
  });
  h.confirmAnswers.push(true);
  h.win.nekoVisitCatScene.requestModelReturn();
  await h.settle();
  report.ball_toolbar_return = {
    clicks: nativeReturnClicks,
    tier_after_exit: native.button.getAttribute('data-neko-idle-tier'),
  };

  h.state('started', {
    cat_scene: 2,
    self_cat_asset: 'dev_neko',
    peer_cat_asset: 'yui_neko',
  });
  h.pose('peer', 'sample', 8, 1);
  h.state('ended');
  report.ended = {
    active: h.body.classList.contains('neko-visit-cat-scene-active'),
    hidden: h.root().hidden,
    native_actor: native.container.getAttribute('data-visit-cat-actor'),
    pending_tasks: h.pendingTasks(),
  };

  const earlyPose = {
    type: 'visit_cat_pose', visit_id: VID, actor: 'peer', phase: 'commit',
    motion_id: 9, sample_seq: 2, x_q16: 45000, y_q16: 22000,
    mode: 'idle', facing: 'left', tier: 'cat1', slot: 'idle.cat1',
  };
  const early = createHarness({
    displaySnapshot: {
      type: 'visit_state_change', action: 'departed', visit_id: VID, side: 'guest',
      cat_scene: 2, self_cat_asset: 'dev_neko', peer_cat_asset: 'yui_neko',
    },
    poseSnapshots: [earlyPose],
  });
  report.early_snapshot = {
    active: early.body.classList.contains('neko-visit-cat-scene-active'),
    peer_visible: early.visible(early.card('peer')),
    x: Number.parseFloat(early.card('peer').style.getPropertyValue('--neko-visit-cat-x')),
  };

  const race = createHarness();
  const VID2 = 'BBBBBBBBBBBBBBBBBBBBBB';
  let resolveOldPose;
  race.setPoseHandler((visitId) => {
    if (visitId === VID) return new Promise((resolve) => { resolveOldPose = resolve; });
    return { ok: true };
  });
  race.state('started', { cat_scene: 2, self_cat_asset: 'dev_neko', peer_cat_asset: 'yui_neko' });
  race.installNativeSelf();
  race.setSnapshot({
    tier: 'cat1', slot: 'idle.cat1', mode: 'idle', facing: 'left',
    x_q16: 10000, y_q16: 10000,
  });
  race.advance(128);
  await race.settle();
  race.state('ended');
  race.stateFor(VID2, 'started', {
    cat_scene: 2, self_cat_asset: 'dev_neko', peer_cat_asset: 'yui_neko',
  });
  race.setSnapshot({
    tier: 'cat1', slot: 'idle.cat1', mode: 'idle', facing: 'right',
    x_q16: 20000, y_q16: 20000,
  });
  race.advance(128);
  await race.settle();
  resolveOldPose(false);
  await race.settle();
  race.advance(128);
  await race.settle();
  report.cross_visit_pose_results = race.poseCalls.filter((call) => call.visitId === VID2).length;

  process.stdout.write(JSON.stringify(report));
})().catch((error) => {
  console.error(error && error.stack || error);
  process.exitCode = 1;
});
"""


def _run() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = HARNESS.replace("__SCENE_PATH__", json.dumps(str(SCENE_PATH)))
    result = run_node_script(node, script, timeout=30, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def report() -> dict:
    return _run()


def test_scene_requires_v2_and_reuses_delayed_native_self(report):
    assert report["v1_ignored"] is True
    assert report["root_created"] is True
    assert report["body_active"] is True
    assert report["entry_events"] == 1
    assert report["no_action_buttons"] is True
    assert report["native"] == {
        "bound_actor": "self",
        "input_role": "actor",
        "fallback_hidden": True,
        "my_labels": 1,
        "sample_calls": 1,
        "top_label_y": 104,
    }


def test_peer_pose_is_group_aware_and_uses_normalized_coordinates(report):
    sample = report["peer_sample"]
    assert sample["visible"] is True
    assert sample["left"] == pytest.approx(25.0004, abs=0.001)
    assert sample["top"] == pytest.approx(50.0008, abs=0.001)
    assert sample["facing"] == "right"
    assert sample["mode"] == "walk"
    assert sample["asset"] == "yui_neko"
    assert "/cat/yui_neko/movement.cat1.walking.gif" in sample["src"]
    assert sample["top_label_y"] == 0


def test_reliable_commit_blocks_late_sample_but_next_motion_advances(report):
    assert report["late_sample"] == {"ignored_position": True, "ignored_art": True}
    assert report["new_motion"]["accepted"] is True
    assert report["new_motion"]["tier"] == "cat2"
    assert "/cat/yui_neko/drag.cat2.gif" in report["new_motion"]["src"]


def test_native_snapshot_sends_sample_then_stable_commit_only(report):
    calls = report["pose_transport"]
    assert [call["phase"] for call in calls] == ["sample", "commit"]
    assert all(call["visitId"] == "A" * 22 for call in calls)
    assert calls[0]["motion_id"] == calls[1]["motion_id"] == 1
    assert calls[0]["sample_seq"] == 1
    assert calls[1]["sample_seq"] == 2
    assert calls[0]["keys"] == [
        "facing",
        "mode",
        "motion_id",
        "sample_seq",
        "slot",
        "tier",
        "x_q16",
        "y_q16",
    ]
    assert report["geometry_events"] >= 4


def test_model_return_is_guarded_until_exit_is_confirmed(report):
    assert report["cancel_guarded"] is True
    assert report["after_cancel"] == {"commits": 0, "endCalls": 0, "active": True}
    assert report["confirm_guarded"] is True
    assert report["after_confirm"] == {
        "commits": 1,
        "endCalls": ["A" * 22],
        "active": False,
    }
    assert report["request_model_return_exposed"] is True
    assert report["ball_toolbar_return"] == {"clicks": 1, "tier_after_exit": "none"}


def test_ended_cleans_native_binding_animation_and_scene(report):
    assert report["ended"] == {
        "active": False,
        "hidden": True,
        "native_actor": None,
        "pending_tasks": 0,
    }


def test_early_relay_snapshots_restore_scene_and_peer_pose(report):
    assert report["early_snapshot"]["active"] is True
    assert report["early_snapshot"]["peer_visible"] is True
    assert report["early_snapshot"]["x"] == pytest.approx(68.6656, abs=0.001)


def test_old_pose_result_cannot_mutate_the_next_visit(report):
    assert report["cross_visit_pose_results"] == 1


def test_wide_pose_uses_the_wide_actor_box_state(report):
    assert report["wide_slot"] == "action.cat1.play_yarn"
