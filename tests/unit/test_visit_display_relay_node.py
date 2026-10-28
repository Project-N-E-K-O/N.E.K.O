# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Behavioural contracts for the visit display relay."""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RELAY_PATH = PROJECT_ROOT / "static" / "visit" / "display-relay.js"

NODE_SCENARIO = r"""
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__RELAY_PATH__, 'utf8');
const listeners = new Map();
class CustomEventLike {
  constructor(type, init) { this.type = type; this.detail = (init || {}).detail; }
}
const window = {
  location: { pathname: '/' },
  lanlan_config: { lanlan_name: 'Mimi' },
  addEventListener(type, fn) {
    const handlers = listeners.get(type) || [];
    handlers.push(fn);
    listeners.set(type, handlers);
  },
  removeEventListener(type, fn) {
    const handlers = listeners.get(type) || [];
    const index = handlers.indexOf(fn);
    if (index >= 0) handlers.splice(index, 1);
  },
  dispatchEvent(event) {
    (listeners.get(event.type) || []).slice().forEach((fn) => fn(event));
    return true;
  },
};
window.window = window;
const sandbox = { window, CustomEvent: CustomEventLike, Set, Object, Number };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'display-relay.js' });

const received = [];
window.addEventListener('neko-visit-cat-world-intent', (event) => received.push(event.detail));
function send(kind, intentId, x, y) {
  window.dispatchEvent(new CustomEventLike('neko-visit-frame', { detail: {
    type: 'visit_cat_world_intent', visit_id: 'AAAAAAAAAAAAAAAAAAAAAA', actor: 'peer',
    kind, intent_id: intentId, x_q16: x, y_q16: y,
  } }));
}

send('pointer', 7, 1000, 2000);
send('pointer', 7, 1000, 2000);
send('pointer', 7, 3000, 4000);
send('pointer', 7, 1000, 2000);
assert.deepEqual(received.map((frame) => [frame.x_q16, frame.y_q16]), [
  [1000, 2000], [3000, 4000], [1000, 2000],
]);

send('yarn_grab', 8, 5000, 6000);
send('yarn_grab', 8, 5000, 6000);
assert.equal(received.filter((frame) => frame.kind === 'yarn_grab').length, 1);
process.stdout.write(JSON.stringify({ received: received.length }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_pointer_deduplication_only_drops_an_adjacent_duplicate() -> None:
    node = shutil.which("node")
    assert node is not None
    completed = run_node_script(
        node,
        NODE_SCENARIO.replace("__RELAY_PATH__", json.dumps(str(RELAY_PATH))),
        timeout=10,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"received": 4}
