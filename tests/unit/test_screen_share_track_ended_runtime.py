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
"""A manual screen share ended by the browser or the system fully resets and tells the backend.

The real ``static/app/app-screen.js`` runs under node with stubbed DOM and
media devices. Each test starts a manual share, then ends its video track the
way Chromium does when the user clicks the browser's "stop sharing" or the
shared window goes away. Three handlers can see that ``ended``: the track's
``onended`` set by the share itself, and the ``ended`` listeners added when
the stream was acquired through Electron's ``getUserMedia`` or through
``getDisplayMedia``. Each must leave the share fully stopped (button, stop
button, frame sender, cached stream), send one ``screen_share_stopped``, and
must not start the voice-time vision heartbeat on its own.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_stdin

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_HARNESS = r"""
const fs = require('node:fs'); const vm = require('node:vm'); const path = require('node:path');
const repo = __REPO__;
__CORE__
async function startShare(way) {
  const r = boot(way === 'electron_listener' ? 'electron' : 'web');
  const stream = await r.w.acquireOrReuseCachedStream({ allowPrompt: true });
  if (!stream) throw new Error('no stream for ' + way);
  await r.w.startScreenSharing();
  return { r, stream };
}
function snapshot(r) {
  const screen = r.w.document.getElementById('screenButton');
  return {
    active: screen.classList.contains('active'),
    stopDisabled: r.w.document.getElementById('stopButton').disabled,
    sender: r.S.videoSenderInterval != null,
    streamCleared: r.S.screenCaptureStream === null,
    notices: r.sent.filter(m => m.action === 'screen_share_stopped').length,
    heartbeatStarts: r.heartbeat.starts,
  };
}
async function endedWay(way) {
  const { r, stream } = await startShare(way);
  const before = snapshot(r);
  // Chromium dispatches one 'ended' to both the onended handler and the
  // listeners; fire the handler under test first, then the other one, which
  // must find nothing left to do.
  if (way === 'onended') stream.track.fireOnended(); else stream.track.fireListeners();
  // The handler under test alone must leave the share stopped.
  const afterFirst = snapshot(r);
  if (way === 'onended') stream.track.fireListeners(); else stream.track.fireOnended();
  const after = snapshot(r);
  // A fully released share can be started again right away.
  await r.w.acquireOrReuseCachedStream({ allowPrompt: true });
  await r.w.startScreenSharing();
  const restarted = snapshot(r);
  return { way, acquired: r.acquired.slice(), before, afterFirst, after, restarted };
}
async function cachedOnly() {
  // Stream cached for proactive vision only: no manual share is running.
  const r = boot('web');
  const stream = await r.w.acquireOrReuseCachedStream({ allowPrompt: true });
  stream.track.fireListeners();
  return snapshot(r);
}
(async () => {
  const way = process.argv[2] || __WAY__;
  let out;
  if (way === 'cached_only') out = await cachedOnly();
  else if (way === 'hangup') {
    // What ending the voice chat does first (stopRecording / the hang-up
    // button): teardownScreenSharing, then end_session / pause_session.
    const { r } = await startShare('display_listener');
    await new Promise(res => setImmediate(res));
    const ticks = Array.from(r.intervals.values());
    for (const tick of ticks) await tick();
    const framesBefore = r.sent.filter(m => m.action === 'stream_data').length;
    r.w.teardownScreenSharing();
    r.S.isRecording = false;
    r.S.voiceChatActive = false;
    // A tick that was already queued when the interval was cleared.
    for (const tick of ticks) await tick();
    out = Object.assign(snapshot(r), {
      framesBefore,
      framesAfter: r.sent.filter(m => m.action === 'stream_data').length - framesBefore,
      liveTagged: r.sent.filter(m => m.action === 'stream_data').every(m => m.live_share === true),
    });
  }
  else if (way === 'ended_during_pending_start') {
    // The cached (proactive vision) stream ends while a share start still waits
    // on audio init: the start is not cancelled (it has not adopted the stream).
    const r = boot('web');
    const cached = await r.w.acquireOrReuseCachedStream({ allowPrompt: true });
    let open; r.setAudioGate(new Promise(res => { open = res; }));
    const starting = r.w.startScreenSharing();
    await new Promise(res => setImmediate(res));
    cached.track.fireListeners();
    const during = snapshot(r);
    r.setAudioGate(null); open();
    await starting;
    out = { during, after: snapshot(r), acquired: r.acquired.slice() };
  }
  else if (way === 'storage_source_change') {
    // Another window picked a different source: this window's share stops.
    const { r } = await startShare('display_listener');
    (r.windowListeners.storage || []).forEach(fn => fn({ key: 'selectedScreenSourceId', newValue: 'screen:9:0' }));
    out = snapshot(r);
  }
  else if (way === 'build_message') {
    const r = boot('web');
    out = ['screen', 'camera'].map(t => r.w.appScreen.buildStreamDataMessage('data:image/jpeg;base64,AAA', t, null, null));
  } else out = await endedWay(way);
  console.log(JSON.stringify(out));
})().catch(e => { console.error('ERR', e && e.stack || e); process.exit(1); });
"""

_CORE = r"""
function makeTrack() {
  const listeners = [];
  return { kind: 'video', readyState: 'live', onended: null, stops: 0,
    addEventListener(type, fn) { if (type === 'ended') listeners.push(fn); },
    stop() { this.stops += 1; this.readyState = 'ended'; },
    fireListeners() { this.readyState = 'ended'; listeners.slice().forEach(fn => fn()); },
    fireOnended() { this.readyState = 'ended'; if (typeof this.onended === 'function') this.onended(); },
    listenerCount: () => listeners.length };
}
function makeStream() { const t = makeTrack(); return { active: true, track: t, getVideoTracks: () => [t], getTracks: () => [t] }; }
function boot(mode) {
  const sent = []; const noop = () => {};
  const S = { isRecording: true, voiceChatActive: true, proactiveVisionEnabled: true, proactiveVisionInterval: 15,
    socket: { readyState: 1, send: m => sent.push(JSON.parse(m)) }, audioPlayerContext: { state: 'running' },
    screenCaptureStream: null, selectedScreenSourceId: mode === 'electron' ? 'screen:0:0' : null };
  const el = id => { const c = new Set(); return { id, disabled: false, play: async () => {}, getContext: () => ({ drawImage() {}, getImageData: () => ({ data: [200, 200, 200, 255] }) }), toDataURL: () => 'data:image/jpeg;base64,AAA', srcObject: null, readyState: 4, videoWidth: 4, videoHeight: 4, style: {}, dataset: {}, classList: { add: x => c.add(x), remove: x => c.delete(x), contains: x => c.has(x), toggle() {} },
    addEventListener: noop, removeEventListener: noop, setAttribute: noop, getAttribute: () => null, appendChild: noop, querySelector: () => null, querySelectorAll: () => [] }; };
  const els = {};
  const document = { body: el('body'), documentElement: el('html'), addEventListener: noop, hasFocus: () => true, activeElement: null, hidden: false, visibilityState: 'visible',
    getElementById: id => (els[id] = els[id] || el(id)), querySelector: () => null, querySelectorAll: () => [], createElement: t => el(t) };
  let lastStream = null; const acquired = []; const intervals = new Map(); let iid = 1;
  const mediaDevices = {
    getDisplayMedia: async () => { acquired.push('display'); return (lastStream = makeStream()); },
    getUserMedia: async () => { acquired.push('electron'); return (lastStream = makeStream()); },
  };
  const heartbeat = { starts: 0 }; const windowListeners = {}; let audioGate = null; 
  const w = { console: { log: noop, info: noop, debug: noop, warn: noop, error: noop },
    location: { pathname: '/', origin: 'http://x', search: '' }, appState: S, appConst: { MAX_SCREENSHOT_WIDTH: 1280, MAX_SCREENSHOT_HEIGHT: 720 },
    appUtils: { isMobile: () => false }, safeT: (k, f) => f, t: k => k, document, navigator: { userAgent: 'node', mediaDevices },
    WebSocket: { OPEN: 1 }, lanlan_config: { lanlan_name: 'T' },
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop }, sessionStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    addEventListener(type, fn) { (windowListeners[type] = windowListeners[type] || []).push(fn); }, removeEventListener: noop, dispatchEvent: noop, CustomEvent: function () {},
    setTimeout: () => 0, clearTimeout: noop, setInterval(fn) { const id = iid++; intervals.set(id, fn); return id; }, clearInterval(id) { intervals.delete(id); },
    showStatusToast: noop, ensureAudioPlayerContext: async () => { if (audioGate) await audioGate; },
    startProactiveVisionDuringSpeech: () => { heartbeat.starts += 1; }, stopProactiveVisionDuringSpeech: noop,
    invokeDesktopCaptureWithTimeout: async (p, m, args) => p[m].apply(p, args), desktopSourceEnumerationMayPrompt: () => false,
  };
  if (mode === 'electron') {
    w.getDesktopCaptureProvider = () => ({ getSources: async () => [{ id: 'screen:0:0', name: 'Screen 1' }] });
  }
  w.window = w; vm.createContext(w);
  vm.runInContext(fs.readFileSync(path.join(repo, 'static/app/app-screen.js'), 'utf8'), w);
  if (mode === 'electron') S.selectedScreenSourceId = 'screen:0:0';
  return { w, S, sent, els, intervals, heartbeat, acquired, windowListeners, setAudioGate: g => { audioGate = g; }, lastStreamRef: () => lastStream };
}
async function run(way) {
  const mode = way === 'electron_listener' ? 'electron' : 'web';
  const r = boot(mode);
  const stream = await r.w.acquireOrReuseCachedStream({ allowPrompt: true });
  if (!stream) throw new Error('no stream for ' + way);
  await r.w.startScreenSharing();
  const screen = r.w.document.getElementById('screenButton');
  const before = { active: screen.classList.contains('active'), sender: r.S.videoSenderInterval != null };
  if (way === 'onended') stream.track.fireOnended(); else stream.track.fireListeners();
  // A second dispatch of the same 'ended' (listener + onended) must stay idempotent.
  if (way === 'onended') stream.track.fireListeners(); else stream.track.fireOnended();
  return { way, acquired: r.acquired.slice(), before, after: {
    active: screen.classList.contains('active'),
    stopDisabled: r.w.document.getElementById('stopButton').disabled,
    sender: r.S.videoSenderInterval != null,
    stream: r.S.screenCaptureStream === null,
    stillEngaged: typeof r.w.appScreen.isScreenShareRunningOrStarting === 'function' ? null : null,
    notices: r.sent.filter(m => m.action === 'screen_share_stopped').length,
    liveFrames: r.sent.filter(m => m.action === 'stream_data').every(m => m.live_share === true),
    heartbeatStarts: r.heartbeat.starts,
  } };
}
"""


def _node() -> str:
    found = shutil.which("node")
    if not found:
        pytest.skip("node not available")
    return found


def _run(way: str) -> dict:
    script = (
        _HARNESS.replace("__CORE__", _CORE)
        .replace("__REPO__", json.dumps(str(PROJECT_ROOT)))
        .replace("__WAY__", json.dumps(way))
    )
    proc = run_node_stdin(_node(), script, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    ("way", "acquired"),
    [
        ("onended", "display"),
        ("display_listener", "display"),
        ("electron_listener", "electron"),
    ],
)
def test_track_ended_fully_stops_share_and_notifies_backend(way, acquired):
    out = _run(way)
    assert out["acquired"][0] == acquired
    before, after = out["before"], out["after"]
    first = out["afterFirst"]
    assert first["active"] is False and first["sender"] is False
    assert first["streamCleared"] is True and first["notices"] == 1
    assert before["active"] and before["sender"] and not before["stopDisabled"]
    assert after["active"] is False
    assert after["stopDisabled"] is True
    assert after["sender"] is False
    assert after["streamCleared"] is True
    assert after["notices"] == 1
    # Ending the share must not start the speech-time vision heartbeat.
    assert after["heartbeatStarts"] == 0
    assert out["restarted"]["active"] is True


def test_ended_stream_cached_only_for_proactive_vision_sends_no_notice():
    out = _run("cached_only")
    assert out["streamCleared"] is True
    assert out["notices"] == 0


def test_continuous_share_frames_are_tagged_live():
    """Manual share and the voice vision heartbeat both build frames here."""
    for msg in _run("build_message"):
        assert msg["action"] == "stream_data"
        assert msg["live_share"] is True
        assert "request_id" not in msg


def test_share_stopped_by_another_windows_source_change_notifies_backend():
    out = _run("storage_source_change")
    assert out["active"] is False
    assert out["sender"] is False
    assert out["notices"] == 1


def test_hanging_up_voice_stops_share_frames_before_the_session_ends():
    """Ending voice chat tears the share down first; no frame follows the notice."""
    out = _run("hangup")
    assert out["framesBefore"] >= 1 and out["liveTagged"] is True
    assert out["framesAfter"] == 0
    assert out["notices"] == 1
    # Button styling is the hang-up caller's job; the frame sender is gone.
    assert out["sender"] is False


def test_cached_stream_ending_during_a_pending_start_does_not_cancel_it():
    """Same as before this change: the start owns no stream yet, so it is not torn down."""
    out = _run("ended_during_pending_start")
    assert out["during"]["notices"] == 0 and out["after"]["notices"] == 0
    # A torn-down start disables the stop button (clearScreenSharingIndicators).
    assert out["during"]["stopDisabled"] is False
    assert out["after"]["stopDisabled"] is False
