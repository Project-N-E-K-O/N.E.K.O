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
"""Ownership of the shared voice-start slot.

``S.sessionStartedResolver`` / ``Rejecter`` / ``_pendingSessionStartMode`` are
ONE slot, and concurrent starts genuinely exist: the mic button, the composer's
text send, the avatar-drop text entry and the automatic reconnect restart can
all be in flight together. Every flow used to clear the slot unconditionally on
its way out, so whichever finished first wiped whoever owned it -- the newer
start then hung on a promise nobody would settle, or lost its timeout.

The owner token is the resolver function itself. These cases drive the real
helpers from app-state.js rather than asserting on source text, because the
property that matters is behavioural: a release by a superseded flow must be a
no-op.
"""

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

APP_STATE_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-state.js"

_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');

function assert(cond, msg) {
  if (!cond) throw new Error('ASSERT: ' + msg);
}

// app-state.js is a large IIFE with browser dependencies; the ownership helpers
// are self-contained, so lift just those out and run them against a stub S.
const source = fs.readFileSync(__APP_STATE_PATH__, 'utf8');
const start = source.indexOf('window.claimSessionStart = function');
const end = source.indexOf('window.cancelPendingSessionStart = function');
assert(start > 0 && end > start, 'could not locate the ownership helpers');

const S = {
  sessionStartedResolver: null,
  sessionStartedRejecter: null,
  _pendingSessionStartMode: null,
};
const sandbox = { S, window: {}, console: { log() {}, warn() {} } };
vm.createContext(sandbox);
vm.runInContext(source.slice(start, end), sandbox, { filename: 'app-state-helpers.js' });
const W = sandbox.window;

// --- a superseded flow must not release the newer start's slot -------------
const firstResolve = () => {};
const firstReject = () => {};
const firstOwner = W.claimSessionStart('audio', firstResolve, firstReject);
assert(S.sessionStartedResolver === firstResolve, 'the first start claimed the slot');

const secondResolve = () => {};
const secondReject = () => {};
const secondOwner = W.claimSessionStart('text', secondResolve, secondReject);
assert(S.sessionStartedResolver === secondResolve, 'the second start took the slot');
assert(S._pendingSessionStartMode === 'text', 'the mode follows the newest start');

assert(W.sessionStartIsCurrent(firstOwner) === false,
       'the superseded start must not report itself current');
assert(W.sessionStartIsCurrent(secondOwner) === true,
       'the owning start must report itself current');

assert(W.releaseSessionStart(firstOwner) === false,
       'a superseded flow releasing must be refused');
assert(S.sessionStartedResolver === secondResolve,
       'the newer start must still own the slot after a foreign release');
assert(S.sessionStartedRejecter === secondReject, 'and keep its rejecter');
assert(S._pendingSessionStartMode === 'text', 'and keep its mode');

// --- the owner CAN release, exactly once -----------------------------------
assert(W.releaseSessionStart(secondOwner) === true, 'the owner may release');
assert(S.sessionStartedResolver === null, 'the slot is cleared by its owner');
assert(S.sessionStartedRejecter === null, 'rejecter cleared too');
assert(S._pendingSessionStartMode === null, 'mode cleared too');
assert(W.releaseSessionStart(secondOwner) === false,
       'a second release by the same owner is a no-op, not a clear of whoever came next');

// --- a null/absent owner can never clear -----------------------------------
W.claimSessionStart('audio', firstResolve, firstReject);
assert(W.releaseSessionStart(null) === false, 'a missing token must not clear the slot');
assert(W.releaseSessionStart(undefined) === false, 'nor an undefined one');
assert(S.sessionStartedResolver === firstResolve, 'slot survives both');
assert(W.sessionStartIsCurrent(null) === false, 'a missing token is never current');

console.log('HARNESS_OK');
"""


def _run(script: str):
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping voice-start ownership harness")
    return run_node_script(
        node_path,
        script,
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.unit
def test_a_superseded_flow_cannot_release_the_newer_starts_slot():
    # Mutation-verified: drop the identity check in releaseSessionStart and this
    # reddens on "a superseded flow releasing must be refused".
    harness = textwrap.dedent(_HARNESS).replace(
        "__APP_STATE_PATH__", json.dumps(str(APP_STATE_PATH))
    )
    result = _run(harness)
    assert result.returncode == 0, (
        "voice-start ownership harness failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "HARNESS_OK" in result.stdout
