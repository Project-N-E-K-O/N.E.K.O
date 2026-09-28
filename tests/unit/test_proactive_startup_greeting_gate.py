"""Proactive chat must not talk over the startup / character-switch greeting.

``greeting_check`` arms a gate in app-proactive.js. It follows the first
assistant turn that STARTED after arming: it opens when that turn's speech
ends (or is cancelled / unavailable), a few seconds after its text ends if no
speech ever starts, and as soon as the backend reports ``greeting_check_done``
before any greeting turn started (it decided not to greet: a refresh, a recent
conversation, ...). ``STARTUP_GREETING_GATE_MAX_MS`` is the fallback when that
report never arrives. While it holds, both proactive timer branches skip
exactly like the "assistant is speaking" guard.
"""
from __future__ import annotations

import asyncio
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
const fire = (type, turnId) => window.dispatchEvent({ type, detail: turnId ? { turnId } : {} });
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
now += 7_999;
out.textOnlyBeforeGrace = P.isStartupGreetingGateHolding();
now += 1;
out.textOnlyAtGrace = P.isStartupGreetingGateHolding();

// 5b. Audio for the greeting has arrived but its decode / queueing is slow:
//     past the text-only grace the gate still holds until the speech ends.
P.armStartupGreetingGate('ws-open');
fire('neko-assistant-turn-start', 'greeting-turn');
fire('neko-assistant-turn-end', 'greeting-turn');
P.noteStartupGreetingAudio('greeting-turn');
now += 20_000;
out.audioArrivedSlowDecode = P.isStartupGreetingGateHolding();
fire('neko-assistant-speech-start', 'greeting-turn');
fire('neko-assistant-speech-end', 'greeting-turn');
out.audioArrivedAfterSpeechEnd = P.isStartupGreetingGateHolding();

// 5c. Late queued audio of an earlier turn arrives during a text-only
//     greeting: it is not the greeting's audio, so the grace still opens it.
P.armStartupGreetingGate('ws-open');
fire('neko-assistant-turn-start', 'greeting-turn');
P.noteStartupGreetingAudio('previous-turn');
fire('neko-assistant-turn-end', 'greeting-turn');
now += 8_000;
out.staleAudioTextOnlyAtGrace = P.isStartupGreetingGateHolding();

// 6. TTS unavailable for the greeting turn: opens at once.
P.armStartupGreetingGate('ws-open');
fire('neko-assistant-turn-start');
fire('neko-assistant-speech-unavailable');
out.afterSpeechUnavailable = P.isStartupGreetingGateHolding();

// 6b. Character switch while the previous reply is still playing: that
//     reply's speech-end / turn-end (another turnId) must not open the gate.
P.armStartupGreetingGate('character-switch');
fire('neko-assistant-turn-start', 'greeting-turn');
fire('neko-assistant-speech-end', 'previous-turn');
fire('neko-assistant-turn-end', 'previous-turn');
out.previousTurnSpeechEnd = P.isStartupGreetingGateHolding();
fire('neko-assistant-speech-start', 'greeting-turn');
fire('neko-assistant-speech-end', 'greeting-turn');
out.greetingTurnSpeechEnd = P.isStartupGreetingGateHolding();

// 7. No greeting ever arrives (backend skipped): opens by itself at 45 s.
P.armStartupGreetingGate('ws-open');
now += 44_999;
out.justBeforeCap = P.isStartupGreetingGateHolding();
now += 1;
out.atCap = P.isStartupGreetingGateHolding();

// 7b. The lost-event cap counts from the turn start, not from greeting_check:
//     a turn that starts 40 s in still gets its full 120 s.
P.armStartupGreetingGate('ws-open');
now += 40_000;
fire('neko-assistant-turn-start');
now += 100_000;
out.lateTurnStillWithinItsOwnCap = P.isStartupGreetingGateHolding();
now += 20_000;
out.lateTurnAtItsOwnCap = P.isStartupGreetingGateHolding();

// 8. A greeting that has started is not cut off at 45 s (slow generation /
//    synthesis); only the wider lost-event cap opens it.
P.armStartupGreetingGate('ws-open');
fire('neko-assistant-turn-start');
now += 60_000;
out.startedTurnPast45s = P.isStartupGreetingGateHolding();
now += 60_000;
out.startedTurnAtLostEventCap = P.isStartupGreetingGateHolding();

// 9. The backend settled the check without a greeting (refresh, recent
//    conversation, ...): the gate opens at once instead of waiting 45 s.
P.armStartupGreetingGate('ws-open', 'check-1');
now += 1_000;
P.noteStartupGreetingCheckDone('check-1');
out.checkDoneWithoutGreeting = P.isStartupGreetingGateHolding();

// 9a. A late done for an earlier check (quick character switch / resend), or
//     one without an id, must not open a newer gate.
P.armStartupGreetingGate('character-switch', 'check-3');
P.noteStartupGreetingCheckDone('check-2');
out.staleCheckDone = P.isStartupGreetingGateHolding();
P.noteStartupGreetingCheckDone();
out.checkDoneWithoutId = P.isStartupGreetingGateHolding();
P.noteStartupGreetingCheckDone('check-3');
out.ownCheckDone = P.isStartupGreetingGateHolding();

// 9b. The greeting turn already started: its end, not the check, opens it.
P.armStartupGreetingGate('ws-open', 'check-4');
fire('neko-assistant-turn-start', 'greeting-turn');
P.noteStartupGreetingCheckDone('check-4');
out.checkDoneAfterGreetingStarted = P.isStartupGreetingGateHolding();
fire('neko-assistant-speech-start', 'greeting-turn');
fire('neko-assistant-speech-end', 'greeting-turn');
out.checkDoneThenSpeechEnd = P.isStartupGreetingGateHolding();

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
        "audioArrivedSlowDecode": True,
        "audioArrivedAfterSpeechEnd": False,
        "staleAudioTextOnlyAtGrace": False,
        "afterSpeechUnavailable": False,
        "previousTurnSpeechEnd": True,
        "greetingTurnSpeechEnd": False,
        "justBeforeCap": True,
        "atCap": False,
        "lateTurnStillWithinItsOwnCap": True,
        "lateTurnAtItsOwnCap": False,
        "startedTurnPast45s": True,
        "startedTurnAtLostEventCap": False,
        "checkDoneWithoutGreeting": False,
        "staleCheckDone": True,
        "checkDoneWithoutId": True,
        "ownCheckDone": False,
        "checkDoneAfterGreetingStarted": True,
        "checkDoneThenSpeechEnd": False,
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


def test_both_send_helpers_recheck_the_gate_after_awaiting_headers():
    # 取 CSRF 请求头也是一个等待点：两条发送路径都要在它之后、fetch 之前再查一次。
    source = PROACTIVE_JS.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    for helper, headers_call in (
        ("async function _sendVoiceProactive()", "await voiceProactiveSec.getMutationHeaders()"),
        ("async function _sendProactive()", "await proactiveSec.getMutationHeaders()"),
    ):
        start = source.index(helper)
        headers_at = source.index(headers_call, start)
        fetch_at = source.index("fetch('/api/proactive_chat'", headers_at)
        assert "isStartupGreetingGateHolding()" in source[headers_at:fetch_at], helper


def test_greeting_check_send_arms_the_gate():
    source = WEBSOCKET_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
    send_at = source.index("S.socket.send(JSON.stringify(greetingMessage));")
    arm_at = source.index(
        "window.appProactive.armStartupGreetingGate(greetingReason, greetingCheckId)"
    )
    # Armed right after the send, before any other statement of the send path.
    assert 0 < arm_at - send_at < 300
    # The request carries the id the gate is armed with.
    message = source[source.rindex("var greetingMessage = {", 0, send_at):send_at]
    assert "check_id: greetingCheckId" in message


def test_websocket_reports_each_played_audio_chunk_to_the_gate():
    source = WEBSOCKET_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("response.type === 'audio_chunk'")
    branch = source[start:start + 6000]
    call = branch.index("window.appProactive.noteStartupGreetingAudio(")
    # Only chunks that will play.
    guard = branch.rindex("if (", 0, call)
    assert "!shouldSkip" in branch[guard:call]
    # The gate, the playback queue and the speech_id -> turn record use one
    # resolved turn id. Audio headers carry no turn_id, so a late chunk of an
    # earlier turn resolves to the current turn everywhere: the gate holds
    # while it plays and its speech-end (same turn) opens it, instead of the
    # gate and the speech lifecycle disagreeing until the 120 s cap.
    assert "var chunkTurnId = resolveAssistantLifecycleTurnId(response.turn_id);" in branch
    assert branch[call:call + 200].split("(", 1)[1].lstrip().startswith("chunkTurnId")
    meta = branch[branch.index("S.pendingAudioChunkMetaQueue.push({"):call]
    assert "turnId: chunkTurnId," in meta
    remember = branch.index("rememberAssistantAudioSpeechTurn(", call)
    assert "chunkTurnId" in branch[remember:remember + 200]
    assert branch.count("resolveAssistantLifecycleTurnId(response.turn_id)") == 1 + branch[
        : branch.index("var chunkTurnId")
    ].count("resolveAssistantLifecycleTurnId(response.turn_id)")


def test_websocket_forwards_greeting_check_done_to_the_gate():
    source = WEBSOCKET_JS.read_text(encoding="utf-8")
    start = source.index("response.type === 'greeting_check_done'")
    branch = source[start:start + 400]
    assert "window.appProactive.noteStartupGreetingCheckDone(response.check_id)" in branch


ROUTER_PY = REPO_ROOT / "main_routers" / "websocket_router.py"


def test_every_greeting_check_outcome_reports_done():
    source = ROUTER_PY.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index('elif action == "greeting_check":')
    end = source.index('elif action == "cat_greeting_check":', start)
    handler = source[start:end]
    scheduled = handler.count("_schedule_greeting_task(")
    assert scheduled == 2
    # Each scheduled greeting reports once its task settles ...
    assert handler.count('greeting_check_id = str(message.get("check_id") or "")') == 1
    settled = (
        "_send_greeting_check_done_when_settled(\n"
        "                            lanlan_name, websocket, greeting_check_id\n"
        "                        )"
    )
    assert handler.count(settled) == scheduled
    # ... and the refresh / reconnect skip reports at once.
    skip = handler[handler.index("→ skip (refresh/reconnect)"):]
    assert "await _send_greeting_check_done(websocket, greeting_check_id)" in skip


def test_greeting_check_done_waits_for_the_greeting_task():
    import main_routers.websocket_router as websocket_router

    class _Socket:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(json.loads(text))

    class _ClosedSocket:
        async def send_text(self, text):
            raise RuntimeError("closed")

    def done(check_id):
        return [{"type": "greeting_check_done", "check_id": check_id}]

    async def settle():
        for _ in range(5):
            await asyncio.sleep(0)

    async def scenario():
        websocket_router._greeting_tasks.clear()
        release = asyncio.Event()

        async def greeting():
            await release.wait()

        first, second, late = _Socket(), _Socket(), _Socket()
        try:
            assert websocket_router._schedule_greeting_task("Test", "ordinary", greeting)
            websocket_router._send_greeting_check_done_when_settled("Test", first, "a")
            # Another window's request coalesces onto the task in flight.
            assert not websocket_router._schedule_greeting_task("Test", "ordinary", greeting)
            # Repeated checks from one window keep a single waiter (its
            # latest id) and one callback on the task, however many arrive.
            for check_id in ("b-old-1", "b-old-2", "b"):
                websocket_router._send_greeting_check_done_when_settled(
                    "Test", second, check_id
                )
            task = websocket_router._greeting_tasks["Test"]
            assert len(websocket_router._greeting_done_waiters[task]) == 2
            # A window that disconnects stops waiting and is not written to.
            gone = _Socket()
            websocket_router._send_greeting_check_done_when_settled("Test", gone, "g")
            assert len(websocket_router._greeting_done_waiters[task]) == 3
            websocket_router._forget_greeting_done_waiter(gone)
            assert len(websocket_router._greeting_done_waiters[task]) == 2
            await settle()
            assert first.sent == [] and second.sent == []

            release.set()
            await settle()
            # Each window gets its own request's id back.
            assert first.sent == done("a") and second.sent == done("b")
            assert task not in websocket_router._greeting_done_waiters
            assert gone.sent == []

            # Nothing in flight any more: reported right away.
            websocket_router._send_greeting_check_done_when_settled("Test", late, "c")
            await settle()
            assert late.sent == done("c")
            # A window that is already gone is ignored.
            await websocket_router._send_greeting_check_done(_ClosedSocket(), "d")
        finally:
            release.set()
            for task in list(websocket_router._greeting_tasks.values()):
                task.cancel()
            websocket_router._greeting_tasks.clear()
            websocket_router._greeting_done_waiters.clear()

    asyncio.run(scenario())


def test_disconnect_cleanup_forgets_the_greeting_done_waiter():
    source = ROUTER_PY.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    cleanup = source[source.index("_ws_active_count[lanlan_name] = max(0, "):]
    cleanup = cleanup[: cleanup.index("async with _lock:")]
    assert "_forget_greeting_done_waiter(websocket)" in cleanup
