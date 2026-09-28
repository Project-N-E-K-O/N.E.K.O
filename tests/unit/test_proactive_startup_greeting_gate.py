"""Proactive chat must not talk over the startup / character-switch greeting.

``greeting_check`` arms a gate in app-proactive.js. It opens when the first
assistant turn that STARTED after arming ends, and at the latest after
``STARTUP_GREETING_GATE_MAX_MS`` (the backend may decide not to greet at all,
in which case no turn ever arrives). While it holds, both proactive timer
branches skip exactly like the "assistant is speaking" guard.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

REPO_ROOT = Path(__file__).resolve().parents[2]
PROACTIVE_JS = REPO_ROOT / "static" / "app" / "app-proactive.js"
WEBSOCKET_JS = REPO_ROOT / "static" / "app" / "app-websocket.js"

_HARNESS = r"""
const fs = require('fs');
const listeners = {};
let now = 1_000_000;
const RealDate = Date;
global.Date = class extends RealDate { static now() { return now; } };
global.window = global;
window.appState = {};
window.addEventListener = (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); };
window.removeEventListener = () => {};
window.dispatchEvent = (event) => { (listeners[event.type] || []).forEach((fn) => fn(event)); };
// Module-load timers (leader heartbeats etc.) are irrelevant here and would
// keep node alive; the gate itself reads only Date.now().
window.setInterval = () => 0;
window.setTimeout = () => 0;
window.clearInterval = () => {};
window.clearTimeout = () => {};
global.document = {
  getElementById: () => null, addEventListener() {}, querySelector: () => null,
  querySelectorAll: () => [], hidden: false, visibilityState: 'visible',
};
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.sessionStorage = global.localStorage;
global.location = { pathname: '/', search: '' };
console.log = () => {};
eval(fs.readFileSync(process.env.PROACTIVE_JS, 'utf8'));
const P = window.appProactive;
const fire = (type) => window.dispatchEvent({ type });
const out = {};

// 1. Not armed: never holds.
out.idle = P.isStartupGreetingGateHolding();

// 2. Armed, greeting still being generated: holds.
P.armStartupGreetingGate('ws-open');
out.armed = P.isStartupGreetingGateHolding();

// 3. A turn-end left over from a turn that started BEFORE arming must not open it.
fire('neko-assistant-turn-end');
out.afterStaleTurnEnd = P.isStartupGreetingGateHolding();

// 4. The greeting turn starts and ends: gate opens.
fire('neko-assistant-turn-start');
out.duringGreetingTurn = P.isStartupGreetingGateHolding();
fire('neko-assistant-turn-end');
out.afterGreetingTurn = P.isStartupGreetingGateHolding();

// 5. No greeting ever arrives (backend skipped): opens by itself at 45 s.
P.armStartupGreetingGate('ws-open');
now += 44_999;
out.justBeforeCap = P.isStartupGreetingGateHolding();
now += 1;
out.atCap = P.isStartupGreetingGateHolding();

process.stdout.write(JSON.stringify(out));
process.exit(0);
"""


@pytest.fixture(scope="module")
def node_path():
    path = shutil.which("node")
    if not path:
        pytest.skip("node is not installed")
    return path


def test_gate_opens_on_greeting_turn_end_or_after_45_seconds(node_path, monkeypatch):
    monkeypatch.setenv("PROACTIVE_JS", str(PROACTIVE_JS))
    result = run_node_script(node_path, _HARNESS, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "idle": False,
        "armed": True,
        "afterStaleTurnEnd": True,
        "duringGreetingTurn": True,
        "afterGreetingTurn": False,
        "justBeforeCap": True,
        "atCap": False,
    }


def _timer_guard_blocks(source: str) -> list[str]:
    """Each proactive timer branch's speaking guard plus the statement after it."""
    pattern = re.compile(
        r"if \(_isAssistantSpeaking\(\)\) \{.*?\n\s*\}\n(\s*if \([^\n]*\) \{)",
        re.S,
    )
    return [m.group(1).strip() for m in pattern.finditer(source)]


def test_both_timer_branches_consult_the_gate_after_the_speaking_guard():
    source = PROACTIVE_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
    followers = _timer_guard_blocks(source)
    # Voice-mode and text-mode timers.
    assert followers == [
        "if (isStartupGreetingGateHolding()) {",
        "if (isStartupGreetingGateHolding()) {",
    ]


def test_greeting_check_send_arms_the_gate():
    source = WEBSOCKET_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
    send_at = source.index("S.socket.send(JSON.stringify(greetingMessage));")
    arm_at = source.index("window.appProactive.armStartupGreetingGate(greetingReason)")
    # Armed right after the send, before any other statement of the send path.
    assert 0 < arm_at - send_at < 300
