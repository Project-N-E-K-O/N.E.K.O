"""Proactive chat must not talk over the startup / character-switch greeting.

``greeting_check`` arms a gate in app-proactive.js. It follows the first
assistant turn that STARTED after arming: it opens when that turn's speech
ends (or is cancelled / unavailable), a few seconds after its text ends if no
speech ever starts, and at the latest after ``STARTUP_GREETING_GATE_MAX_MS``
(the backend may decide not to greet at all, in which case no turn arrives). While it holds, both proactive timer
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

// 3. Events from a turn that started BEFORE arming must not open it.
fire('neko-assistant-turn-end');
fire('neko-assistant-speech-end');
out.afterStaleEvents = P.isStartupGreetingGateHolding();

// 4. Greeting text ends before its first audio chunk: still held through the
//    gap, then speech plays past the text-only grace and its end opens the gate.
fire('neko-assistant-turn-start');
fire('neko-assistant-turn-end');
out.textEndedNoSpeechYet = P.isStartupGreetingGateHolding();
now += 1_000;
fire('neko-assistant-speech-start');
now += 10_000;
out.speakingPastTextGrace = P.isStartupGreetingGateHolding();
fire('neko-assistant-speech-end');
out.afterSpeechEnd = P.isStartupGreetingGateHolding();

// 5. Text-only greeting (no speech ever starts): opens after the grace.
P.armStartupGreetingGate('ws-open');
fire('neko-assistant-turn-start');
fire('neko-assistant-turn-end');
now += 4_999;
out.textOnlyBeforeGrace = P.isStartupGreetingGateHolding();
now += 1;
out.textOnlyAtGrace = P.isStartupGreetingGateHolding();

// 6. TTS unavailable for the greeting turn: opens at once.
P.armStartupGreetingGate('ws-open');
fire('neko-assistant-turn-start');
fire('neko-assistant-speech-unavailable');
out.afterSpeechUnavailable = P.isStartupGreetingGateHolding();

// 7. No greeting ever arrives (backend skipped): opens by itself at 45 s.
P.armStartupGreetingGate('ws-open');
now += 44_999;
out.justBeforeCap = P.isStartupGreetingGateHolding();
now += 1;
out.atCap = P.isStartupGreetingGateHolding();

// 8. A greeting that has started is not cut off at 45 s (slow generation /
//    synthesis); only the wider lost-event cap opens it.
P.armStartupGreetingGate('ws-open');
fire('neko-assistant-turn-start');
now += 60_000;
out.startedTurnPast45s = P.isStartupGreetingGateHolding();
now += 60_000;
out.startedTurnAtLostEventCap = P.isStartupGreetingGateHolding();

process.stdout.write(JSON.stringify(out));
process.exit(0);
"""


@pytest.fixture(scope="module")
def node_path():
    path = shutil.which("node")
    if not path:
        pytest.skip("node is not installed")
    return path


def test_gate_follows_the_greeting_speech_with_text_only_and_45s_fallbacks(node_path, monkeypatch):
    monkeypatch.setenv("PROACTIVE_JS", str(PROACTIVE_JS))
    result = run_node_script(node_path, _HARNESS, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "idle": False,
        "armed": True,
        "afterStaleEvents": True,
        "textEndedNoSpeechYet": True,
        "speakingPastTextGrace": True,
        "afterSpeechEnd": False,
        "textOnlyBeforeGrace": True,
        "textOnlyAtGrace": False,
        "afterSpeechUnavailable": False,
        "justBeforeCap": True,
        "atCap": False,
        "startedTurnPast45s": True,
        "startedTurnAtLostEventCap": False,
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


def test_text_trigger_rechecks_the_gate_right_before_sending():
    source = PROACTIVE_JS.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    final_check = source.index("// 发送请求前最终检查：确保功能状态未在 await 期间改变")
    send = source.index("var response = await _sendProactive();", final_check)
    assert "isStartupGreetingGateHolding()" in source[final_check:send]


def test_greeting_check_send_arms_the_gate():
    source = WEBSOCKET_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
    send_at = source.index("S.socket.send(JSON.stringify(greetingMessage));")
    arm_at = source.index("window.appProactive.armStartupGreetingGate(greetingReason)")
    # Armed right after the send, before any other statement of the send path.
    assert 0 < arm_at - send_at < 300
