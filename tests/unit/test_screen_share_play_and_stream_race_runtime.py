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
"""Screen share start failures and concurrent stream acquisition (#3797, #3798).

The real ``static/app/app-screen.js`` runs under node with stubbed DOM and
media devices whose promises the test settles by hand.

* #3797: when ``video.play()`` rejects, no frame sender is ever installed. The
  share must stop fully (button, cached stream) and tell the backend with
  ``screen_share_stopped`` instead of staying on "sharing".
* #3798: two acquisitions in flight at once (proactive vision and a manual
  share, or two proactive captures) must not let the later stream overwrite a
  live stream already cached; the redundant stream's tracks are stopped.
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
let streamSeq = 0;
function makeTrack() {
  return { kind: 'video', readyState: 'live', onended: null, stops: 0,
    addEventListener() {}, stop() { this.stops += 1; this.readyState = 'ended'; } };
}
function makeStream() {
  const t = makeTrack();
  return { id: 's' + (++streamSeq), active: true, track: t, getVideoTracks: () => [t], getTracks: () => [t] };
}
const flush = async () => { for (let i = 0; i < 20; i++) await new Promise(res => setImmediate(res)); };
function boot(mode, opts) {
  opts = opts || {};
  const sent = []; const noop = () => {};
  const S = { isRecording: true, voiceChatActive: true, proactiveVisionEnabled: true, proactiveVisionInterval: 15,
    socket: { readyState: 1, send: m => sent.push(JSON.parse(m)) }, audioPlayerContext: { state: 'running' },
    screenCaptureStream: null, selectedScreenSourceId: mode === 'electron' ? 'screen:0:0' : null };
  const playCalls = [];
  const el = id => { const c = new Set(); return { id, disabled: false,
    play: () => { playCalls.push(id); return opts.playRejects ? Promise.reject(Object.assign(new Error('play failed'), { name: 'NotAllowedError' })) : Promise.resolve(); },
    getContext: () => ({ drawImage() {}, getImageData: () => ({ data: [200, 200, 200, 255] }) }), toDataURL: () => 'data:image/jpeg;base64,AAA', srcObject: null, readyState: 4, videoWidth: 4, videoHeight: 4, style: {}, dataset: {}, classList: { add: x => c.add(x), remove: x => c.delete(x), contains: x => c.has(x), toggle() {} },
    addEventListener: noop, removeEventListener: noop, setAttribute: noop, getAttribute: () => null, appendChild: noop, querySelector: () => null, querySelectorAll: () => [] }; };
  const els = {};
  const document = { body: el('body'), documentElement: el('html'), addEventListener: noop, hasFocus: () => true, activeElement: null, hidden: false, visibilityState: 'visible',
    getElementById: id => (els[id] = els[id] || el(id)), querySelector: () => null, querySelectorAll: () => [], createElement: t => el(t) };
  // Each capture request waits until the test settles it, in any order.
  const pending = [];
  const capture = kind => new Promise(resolve => { pending.push({ kind, resolve: () => { const s = makeStream(); resolve(s); return s; } }); });
  const mediaDevices = { getDisplayMedia: async () => capture('display'), getUserMedia: async () => capture('electron') };
  const intervals = new Map(); let iid = 1;
  const w = { console: { log: noop, info: noop, debug: noop, warn: noop, error: noop },
    location: { pathname: '/', origin: 'http://x', search: '' }, appState: S, appConst: { MAX_SCREENSHOT_WIDTH: 1280, MAX_SCREENSHOT_HEIGHT: 720 },
    appUtils: { isMobile: () => false }, safeT: (k, f) => f, t: k => k, document, navigator: { userAgent: 'node', mediaDevices },
    WebSocket: { OPEN: 1 }, lanlan_config: { lanlan_name: 'T' },
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop }, sessionStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    addEventListener: noop, removeEventListener: noop, dispatchEvent: noop, CustomEvent: function () {},
    setTimeout: () => 0, clearTimeout: noop, setInterval(fn) { const id = iid++; intervals.set(id, fn); return id; }, clearInterval(id) { intervals.delete(id); },
    showStatusToast: noop, ensureAudioPlayerContext: async () => {},
    startProactiveVisionDuringSpeech: noop, stopProactiveVisionDuringSpeech: noop,
    invokeDesktopCaptureWithTimeout: async (p, m, args) => p[m].apply(p, args), desktopSourceEnumerationMayPrompt: () => false,
  };
  if (mode === 'electron') {
    w.getDesktopCaptureProvider = () => ({ getSources: async () => [{ id: 'screen:0:0', name: 'Screen 1' }] });
  }
  w.window = w; vm.createContext(w);
  vm.runInContext(fs.readFileSync(path.join(repo, 'static/app/app-screen.js'), 'utf8'), w);
  if (mode === 'electron') S.selectedScreenSourceId = 'screen:0:0';
  return { w, S, sent, els, intervals, pending, playCalls };
}
function snapshot(r) {
  const screen = r.w.document.getElementById('screenButton');
  return {
    active: screen.classList.contains('active'),
    stopDisabled: r.w.document.getElementById('stopButton').disabled,
    sender: r.S.videoSenderInterval != null,
    streamCleared: r.S.screenCaptureStream === null,
    notices: r.sent.filter(m => m.action === 'screen_share_stopped').length,
  };
}
async function tickSender(r) {
  for (const tick of Array.from(r.intervals.values())) await tick();
  return r.sent.filter(m => m.action === 'stream_data').length;
}
async function waitPending(r, n) {
  for (let i = 0; i < 200 && r.pending.length < n; i++) await new Promise(res => setImmediate(res));
  if (r.pending.length < n) throw new Error('expected ' + n + ' pending captures, got ' + r.pending.length);
}

async function playRejects(playFails) {
  const r = boot('web', { playRejects: playFails });
  const starting = r.w.startScreenSharing();
  await waitPending(r, 1);
  const stream = r.pending[0].resolve();
  await starting;
  await flush();
  return Object.assign(snapshot(r), { playCalls: r.playCalls.length, trackStops: stream.track.stops });
}

// Two proactive captures in flight; the second request is settled first.
async function twoAcquisitions(mode) {
  const r = boot(mode);
  const first = r.w.acquireOrReuseCachedStream({ allowPrompt: true });
  await waitPending(r, 1);
  const second = r.w.acquireOrReuseCachedStream({ allowPrompt: true });
  await waitPending(r, 2);
  const secondStream = r.pending[1].resolve();
  const secondResult = await second;
  const firstStream = r.pending[0].resolve();
  const firstResult = await first;
  return {
    kinds: r.pending.map(p => p.kind),
    secondGotOwn: secondResult === secondStream,
    firstGotCached: firstResult === secondStream,
    cacheIsLive: r.S.screenCaptureStream === secondStream,
    liveStops: secondStream.track.stops,
    redundantStops: firstStream.track.stops,
  };
}

// The issue's sequence: proactive vision starts acquiring with an empty cache,
// the user starts a manual share that acquires its own stream; `order` says
// which of the two capture requests returns first.
async function manualVsProactive(order) {
  const r = boot('web');
  const proactive = r.w.acquireOrReuseCachedStream({ allowPrompt: true });
  await waitPending(r, 1);
  const starting = r.w.startScreenSharing();
  await waitPending(r, 2);
  let proactiveStream, manualStream;
  if (order === 'manual_first') {
    manualStream = r.pending[1].resolve();
    await starting;
    proactiveStream = r.pending[0].resolve();
    await proactive;
  } else {
    proactiveStream = r.pending[0].resolve();
    await proactive;
    manualStream = r.pending[1].resolve();
    await starting;
  }
  await flush();
  const frames = await tickSender(r);
  const during = Object.assign(snapshot(r), {
    cacheIsManual: r.S.screenCaptureStream === manualStream,
    manualStops: manualStream.track.stops,
    proactiveStops: proactiveStream.track.stops,
    frames,
  });
  await r.w.stopScreenSharing(true);
  return { during, after: Object.assign(snapshot(r), {
    manualStops: manualStream.track.stops, proactiveStops: proactiveStream.track.stops }) };
}

(async () => {
  const way = __WAY__;
  let out;
  if (way === 'play_rejects') out = await playRejects(true);
  else if (way === 'play_resolves') out = await playRejects(false);
  else if (way === 'two_display') out = await twoAcquisitions('web');
  else if (way === 'two_electron') out = await twoAcquisitions('electron');
  else out = await manualVsProactive(way);
  console.log(JSON.stringify(out));
})().catch(e => { console.error('ERR', e && e.stack || e); process.exit(1); });
"""


def _node() -> str:
    found = shutil.which("node")
    if not found:
        pytest.skip("node not available")
    return found


def _run(way: str) -> dict:
    script = _HARNESS.replace("__REPO__", json.dumps(str(PROJECT_ROOT))).replace(
        "__WAY__", json.dumps(way)
    )
    proc = run_node_stdin(_node(), script, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_play_rejection_stops_share_and_notifies_backend():
    out = _run("play_rejects")
    assert out["playCalls"] >= 1
    assert out["active"] is False
    assert out["stopDisabled"] is True
    assert out["sender"] is False
    assert out["streamCleared"] is True
    assert out["trackStops"] == 1
    assert out["notices"] == 1


def test_successful_play_keeps_share_running():
    out = _run("play_resolves")
    assert out["active"] is True
    assert out["stopDisabled"] is False
    assert out["sender"] is True
    assert out["streamCleared"] is False
    assert out["trackStops"] == 0
    assert out["notices"] == 0


@pytest.mark.parametrize(("way", "kind"), [("two_display", "display"), ("two_electron", "electron")])
def test_late_acquisition_does_not_overwrite_live_cached_stream(way, kind):
    out = _run(way)
    assert out["kinds"] == [kind, kind]
    assert out["secondGotOwn"] is True
    assert out["firstGotCached"] is True
    assert out["cacheIsLive"] is True
    assert out["liveStops"] == 0
    assert out["redundantStops"] == 1


@pytest.mark.parametrize("order", ["manual_first", "proactive_first"])
def test_manual_share_and_proactive_capture_race_keeps_one_live_stream(order):
    out = _run(order)
    during, after = out["during"], out["after"]
    assert during["active"] is True and during["sender"] is True
    assert during["cacheIsManual"] is True
    assert during["manualStops"] == 0
    # The redundant proactive stream is stopped exactly once, not leaked.
    assert during["proactiveStops"] == 1
    assert during["frames"] >= 1
    assert during["notices"] == 0
    assert after["active"] is False and after["streamCleared"] is True
    assert after["manualStops"] == 1 and after["proactiveStops"] == 1
    assert after["notices"] == 1
