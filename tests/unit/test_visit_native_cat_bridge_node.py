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

"""Behaviour contract for the read-only local-cat visit bridge."""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = PROJECT_ROOT / "static" / "visit" / "native-cat-bridge.js"

HARNESS = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__BRIDGE_PATH__, 'utf8');

class Classes {
  constructor(values) { this.values = new Set(values || []); }
  contains(name) { return this.values.has(name); }
}

class Element {
  constructor(attrs, classes) {
    this.attrs = Object.assign({}, attrs || {});
    this.classList = new Classes(classes);
    this.style = { display: '' };
    this.rect = { left: 0, top: 0, width: 80, height: 80 };
    this.container = null;
    this.art = null;
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
  closest(selector) { return selector === '[id$="-return-button-container"]' ? this.container : null; }
  querySelector(selector) { return selector === '.neko-idle-return-art' ? this.art : null; }
  getBoundingClientRect() { return Object.assign({}, this.rect); }
}

const SLOT_URLS = {
  'idle.cat1': ['/official/idle-1.gif'],
  'idle.cat2': ['/official/idle-2.gif'],
  'idle.cat3': ['/official/idle-3.gif'],
  'click.cat1': ['/official/click-1.gif'],
  'click.cat2': ['/official/click-2.gif'],
  'click.cat3': ['/official/click-3.gif'],
  'drag.cat1': ['/official/move-1.gif', '/official/move-2.gif'],
  'drag.cat2': ['/official/move-2.gif', '/official/move-3.gif'],
  'drag.cat3': ['/official/move-3.gif', '/official/move-4.gif'],
  'drag.rapid': ['/official/move-5.gif'],
  'movement.cat1.walking': ['/official/walk.gif'],
  'movement.cat1.stretch': ['/official/stretch.gif'],
  'movement.cat1.interactive': ['/official/interactive.gif'],
  'action.cat1.eat': ['/official/eat.gif'],
  'action.cat1.play_yarn': ['/official/play.gif'],
  'playground.cat1.air': ['/official/move-2.gif'],
};

let buttons = [];
let listenerCount = 0;
const win = {
  innerWidth: 800,
  innerHeight: 600,
  location: { href: 'https://local.test/' },
  document: { querySelectorAll(selector) { return selector === '.neko-idle-return-btn' ? buttons : []; } },
  addEventListener() { listenerCount += 1; },
  NekoCatResourceRegistry: {
    selectedAppearanceGroupId: 'dev_neko',
    getAppearanceForGroup(_group, slot) {
      const urls = SLOT_URLS[slot] || [];
      return { available: urls.length > 0, url: urls[0] || null, urls: urls.slice() };
    },
  },
  _getNekoDesktopVirtualViewportSize() { return { width: 400, height: 200 }; },
  _getNekoDesktopVirtualElementRect(element) { return Object.assign({}, element.rect); },
};
win.window = win;

vm.runInNewContext(source, { window: win, globalThis: win, URL, Object, Number, Math, String, Array, Set, Map });

function cat(tier, src, attrs, classes, rect) {
  const container = new Element(attrs, classes);
  container.rect = Object.assign({ left: 100, top: 50, width: 100, height: 100 }, rect || {});
  const button = new Element({ 'data-neko-idle-tier': tier }, classes);
  const art = new Element({ src }, []);
  art.src = src;
  button.container = container;
  button.art = art;
  container.art = art;
  return { button, container, art };
}

function take(entry) {
  buttons = entry ? [entry.button] : [];
  return win.NekoIdleCatVisitBridge.snapshot();
}

const walking = cat('cat1', '/official/walk.gif', {
  'data-neko-cat1-substate': 'walking',
  'data-neko-cat1-facing': 'right',
});
const walkingSnapshot = take(walking);

const dragging = cat('cat2', '/official/move-2.gif', { 'data-dragging': 'true' });
const draggingSnapshot = take(dragging);

const airborne = cat('cat1', '/official/move-2.gif');
airborne.button.__nekoIdleCat1PlaygroundDropState = {
  active: true,
  released: false,
  phase: 'ballistic',
  draggingBodyId: '',
  bodies: new Map([['cat', { grounded: false, dragging: false }]]),
};
const airborneSnapshot = take(airborne);

const edge = cat('cat1', '/official/idle-1.gif');
win.NekoEdgePeekController = { getActiveMode(button) { return button === edge.button ? 'drag-edge' : null; } };
const edgeSnapshot = take(edge);
delete win.NekoEdgePeekController;

const hidden = cat('cat1', '/official/idle-1.gif');
hidden.container.style.display = 'none';
const hiddenSnapshot = take(hidden);

process.stdout.write(JSON.stringify({
  walking: walkingSnapshot,
  dragging: draggingSnapshot,
  airborne: airborneSnapshot,
  edge: edgeSnapshot,
  hidden: hiddenSnapshot,
  apiFrozen: Object.isFrozen(win.NekoIdleCatVisitBridge),
  snapshotFrozen: Object.isFrozen(walkingSnapshot),
  listenerCount,
  keys: Object.keys(walkingSnapshot).sort(),
}));
"""


@pytest.fixture(scope="module")
def report() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = HARNESS.replace("__BRIDGE_PATH__", json.dumps(str(BRIDGE_PATH)))
    result = run_node_script(node, script, timeout=20, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_bridge_reads_native_semantics_and_virtual_q16_without_side_effects(report):
    assert report["walking"] == {
        "tier": "cat1",
        "slot": "movement.cat1.walking",
        "mode": "walk",
        "facing": "right",
        "x_q16": 24576,
        "y_q16": 32768,
    }
    assert report["apiFrozen"] is True
    assert report["snapshotFrozen"] is True
    assert report["listenerCount"] == 0
    assert report["keys"] == ["facing", "mode", "slot", "tier", "x_q16", "y_q16"]


def test_bridge_resolves_ambiguous_official_art_by_tier_and_mode(report):
    assert report["dragging"]["tier"] == "cat2"
    assert report["dragging"]["mode"] == "drag"
    assert report["dragging"]["slot"] == "drag.cat2"
    assert report["airborne"]["mode"] == "airborne"
    assert report["airborne"]["slot"] == "playground.cat1.air"
    assert report["edge"]["mode"] == "edge"
    assert report["hidden"] is None
