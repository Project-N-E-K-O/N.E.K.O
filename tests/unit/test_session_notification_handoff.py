"""Drive the production notification branches through reordered start events."""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


ROOT = Path(__file__).resolve().parents[2]
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = __ROOT__;
const source = fs.readFileSync(root + '/static/app/app-state.js', 'utf8');
const ws = fs.readFileSync(root + '/static/app/app-websocket.js', 'utf8');
const S = {voiceSessionStartEpoch: 0, voiceStartPending: false, voiceChatActive: false};
let shown = 0, hidden = 0, stopped = 0;
const timers = [];
const window = {
 t: x => x, showVoicePreparingToast: () => shown++, hideVoicePreparingToast: () => hidden++,
 stopRecording: options => { assert.equal(options.notifyServer, false); stopped++; },
};
const sandbox = { S, window, console: {log(){}, warn(){}},
 clearTimeout(){}, setTimeout: fn => {timers.push(fn); return timers.length;},
 document: {getElementById: () => null}, micButton: () => null,
 muteButton: () => null, screenButton: () => null, stopButton: () => null,
 resetSessionButton: () => null };
vm.createContext(sandbox);
const begin = source.indexOf('window.makeNekoSessionAbortError = function');
const end = source.indexOf('// ======================== 工具函数');
assert(begin > 0 && end > begin);
vm.runInContext(source.slice(begin, end), sandbox);
const branchStart = ws.indexOf("} else if (response.type === 'session_preparing') {");
const branchEnd = ws.indexOf("// -------- session_ended_by_server --------", branchStart);
assert(branchStart > 0 && branchEnd > branchStart);
vm.runInContext('function receive(response) { if (false) {' + ws.slice(branchStart, branchEnd) + '} }', sandbox);
const receive = sandbox.receive;
let rejected = 0, resolved = 0;
const first = window.claimSessionStart('audio', () => resolved++, () => rejected++);
const firstId = window.sessionStartRequestId(first);
const second = window.claimSessionStart('audio', () => resolved++, () => rejected++);
const secondId = window.sessionStartRequestId(second);
rejected = 0;
S.voiceStartPending = true;
window.sessionTimeoutId = 41;
__CASE__
"""

CASES = {
    "anonymous_success_does_not_settle": """
receive({type:'session_started', input_mode:'audio'});
timers.splice(0).forEach(fn => fn());
assert.equal(resolved, 0);
assert.equal(window.sessionTimeoutId, 41);
assert.equal(S.sessionStartedResolver, second);
""",
    "old_failure": """
receive({type:'session_failed', input_mode:'audio', request_id:firstId});
assert.equal(rejected, 0, 'old failure must not reject the successor');
assert.equal(S.sessionStartedResolver, second);
assert.equal(window.sessionTimeoutId, 41);
assert.equal(S.voiceStartPending, true);
""",
    "old_preparing": """
receive({type:'session_preparing', input_mode:'audio', request_id:firstId});
assert.equal(shown, 0, 'retired preparing must not replace the successor banner');
receive({type:'session_preparing', input_mode:'audio', request_id:secondId});
assert.equal(shown, 1);
""",
    "old_success_after_completed_takeover": """
window.releaseSessionStart(second);
S.voiceChatActive = false;
receive({type:'session_started', input_mode:'audio', request_id:firstId});
assert.equal(S.voiceChatActive, false, 'old success must not resurrect the previous session');
""",
    "deferred_banner": """
receive({type:'session_started', input_mode:'audio', request_id:secondId});
window.claimSessionStart('audio', () => {}, () => {});
S.voiceStartPending = true;
timers.splice(0).forEach(fn => fn());
assert.equal(hidden, 0, 'old ack timer must not hide a newer start banner');
assert.equal(S.voiceStartPending, true);
""",
    "foreign_failure": """
receive({type:'session_failed', input_mode:'audio', request_id:'another-window-1'});
assert.equal(rejected, 0, 'another window failure must not settle our pending request');
assert.equal(S.sessionStartedResolver, second);
""",
    "foreign_text_success_stops_lease_holder": """
S.isRecording = true;
receive({type:'session_started', input_mode:'text', request_id:'another-window-1'});
assert.equal(stopped, 1, 'text takeover must still stop the microphone lease holder');
assert.equal(S.sessionStartedResolver, second);
""",
    "observer_success": """
window.releaseSessionStart(second);
receive({type:'session_started', input_mode:'audio', request_id:'another-window-1'});
assert.equal(S.voiceChatActive, true, 'observers must continue to synchronize session state');
""",
    "matching_failure": """
receive({type:'session_failed', input_mode:'audio', request_id:secondId});
assert.equal(rejected, 1);
assert.equal(S.sessionStartedResolver, null);
assert.equal(S.voiceStartPending, false);
""",
}


@pytest.mark.unit
@pytest.mark.parametrize("case", CASES)
def test_start_notification_ownership(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required")
    script = HARNESS.replace("__ROOT__", json.dumps(str(ROOT))).replace("__CASE__", CASES[case])
    result = run_node_script(node, script, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.unit
@pytest.mark.parametrize("case, protection", [
    ("old_success_after_completed_takeover", "window.sessionStartNotificationIsRetired(response)"),
    ("foreign_failure", "!window.sessionStartNotificationAnswersPending(response)"),
    ("deferred_banner", "!window.sessionStartsSince(_ackedClaimSeq)"),
])
def test_notification_regressions_detect_removed_protection(case, protection):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required")
    source = HARNESS.replace("const ws =", "let ws =")
    replacement = "true" if case == "deferred_banner" else "false"
    mutation = f"ws = ws.replaceAll({json.dumps(protection)}, {json.dumps(replacement)});\n"
    source = source.replace("const branchStart =", mutation + "const branchStart =")
    script = source.replace("__ROOT__", json.dumps(str(ROOT))).replace("__CASE__", CASES[case])
    result = run_node_script(node, script, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
    assert result.returncode != 0, "removing the protection must break its behavioral regression"
    assert "AssertionError" in result.stderr
