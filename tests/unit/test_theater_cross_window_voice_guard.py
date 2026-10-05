"""Ordinary voice must stay blocked while a theater runs in another desktop window.

In Electron the theater runtime activates only in the compact ``/chat`` window,
while the floating microphone lives in the Pet window (``/``).  The mic guards
used to read only the Pet window's own runtime, which is never active there, so
the Pet mic could start ordinary voice mid-performance and hide the theater
composer.  These scenarios run the real ``app-proactive.js``,
``app-theater-runtime.js`` and ``app-audio-capture.js`` inside node ``vm``
contexts wired through a fake ``BroadcastChannel`` and pin the contract:

* the Pet window learns about the other window's theater from the existing
  proactive leader heartbeat and refuses to open the ordinary microphone;
* the block lifts when the theater exits, when its window says goodbye, and
  after the heartbeat TTL when the theater window crashed silently.
"""

from pathlib import Path
import shutil

import pytest

from tests.node_harness import run_node_stdin


ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const clock = { now: 1_000_000 };
const channels = [];
const queue = [];
class FakeBroadcastChannel {
  constructor(name) { this.name = name; this.onmessage = null; this.listeners = []; channels.push(this); }
  addEventListener(type, fn) { if (type === 'message') this.listeners.push(fn); }
  postMessage(data) {
    const copy = JSON.parse(JSON.stringify(data));
    for (const peer of channels) {
      if (peer === this || peer.name !== this.name) continue;
      queue.push(() => {
        if (peer.onmessage) peer.onmessage({ data: copy });
        for (const fn of peer.listeners) fn({ data: copy });
      });
    }
  }
  close() {}
}
// BroadcastChannel 是异步投递；测试显式冲刷，模拟跨窗口消息到达。
function flush() { while (queue.length) queue.shift()(); }
function createWindow(pathname, options = {}) {
  const intervals = [], logs = [], listeners = {};
  let nextTimer = 1;
  const appState = {
    proactiveChatEnabled: true, proactiveNewsChatEnabled: true, proactiveChatInterval: 60,
    proactiveChatBackoffLevel: 0, proactiveChatTimer: null, isRecording: false,
    _proactiveStartupDelayApplied: true,
  };
  const theater = { suppressed: false };
  const node = () => ({ style: {}, classList: { add() {}, remove() {}, contains: () => false },
    appendChild() {}, addEventListener() {}, setAttribute() {} });
  const window = {
    console: { log: (...args) => logs.push(args.join(' ')), info() {}, debug() {}, warn() {}, error() {} },
    location: { pathname, origin: 'https://local.test' },
    appState, appConst: {}, appUtils: {},
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      readyState: 'loading', addEventListener() {}, getElementById: () => null, querySelector: () => null,
      createElement: node, head: node(), activeElement: null, hasFocus: () => true,
      body: { classList: { contains: () => false }, getAttribute: () => null, appendChild() {} },
    },
    navigator: { userAgent: 'node', mediaDevices: { addEventListener() {} } },
    addEventListener: (name, fn) => { listeners[name] = fn; }, dispatchEvent() {},
    setTimeout: () => nextTimer++, clearTimeout() {},
    setInterval: fn => { intervals.push(fn); return 0; }, clearInterval() {},
    fetch: () => Promise.reject(new Error('offline')),
    BroadcastChannel: FakeBroadcastChannel,
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    t: key => key,
    lastUserInputTime: 0,
  };
  window.window = window;
  vm.createContext(window);
  window.__clock = clock;
  vm.runInContext('Date.now = () => globalThis.__clock.now;', window);
  const scripts = ['static/app/app-proactive.js'];
  if (options.realTheater) {
    scripts.push('static/js/theater_transport.js', 'static/app/app-theater-runtime.js', 'static/app/app-audio-capture.js');
  } else {
    // chat.html 中运行剧场的一方：只需回答是否抑制，由 app-proactive.js 随心跳广播。
    window.nekoTheaterRuntime = { suppressesProactiveChat: () => theater.suppressed };
  }
  for (const path of scripts) vm.runInContext(fs.readFileSync(path, 'utf8'), window, { filename: path });
  return {
    window, appState, theater, logs, listeners, proactive: window.appProactive,
    runtime: window.nekoTheaterRuntime,
    heartbeat() { for (const fn of intervals) fn(); },
  };
}
function setupPair() {
  channels.length = 0; queue.length = 0;
  const pet = createWindow('/', { realTheater: true });
  const chat = createWindow('/chat');
  flush();
  assert.equal(pet.runtime.getState().active, false, 'Pet 窗口自身从不激活剧场运行态');
  return { pet, chat };
}
function setChatTheater(chat, value) {
  chat.theater.suppressed = value;
  chat.proactive.refreshProactiveSuppression();
  flush();
}
// 路由 fail-closed 让放行的调用停在守卫之后的第一道闸门，不触碰真实麦克风。
async function micReachesCapture(pet) {
  pet.appState.voiceInputRouteBlocked = true;
  pet.logs.length = 0;
  const result = await pet.window.startMicCapture();
  assert.equal(result, false);
  return pet.logs.some(line => line.includes('voice route is fail-closed'));
}
"""

SCENARIOS = (
    ("pet_mic_blocked_while_chat_window_runs_theater", r"""
      const { pet, chat } = setupPair();
      assert.equal(pet.runtime.blocksOrdinaryVoice(), false);
      assert.equal(await micReachesCapture(pet), true, '无剧场时麦克风应越过剧场守卫（证明下方断言不是空转）');
      setChatTheater(chat, true);
      assert.equal(pet.runtime.blocksOrdinaryVoice(), true, 'Pet 必须感知 chat 窗口中正在进行的剧场');
      assert.equal(await micReachesCapture(pet), false, '剧场期间 Pet 不得打开普通聊天麦克风');
      // 常规心跳持续携带状态，下一拍不能把锁冲掉。
      chat.heartbeat(); flush();
      assert.equal(pet.runtime.blocksOrdinaryVoice(), true);
      setChatTheater(chat, false);
      assert.equal(pet.runtime.blocksOrdinaryVoice(), false, '剧场结束后必须立即解除');
      assert.equal(await micReachesCapture(pet), true);
    """),
    ("crashed_theater_window_releases_mic_after_ttl", r"""
      const { pet, chat } = setupPair();
      setChatTheater(chat, true);
      assert.equal(pet.runtime.blocksOrdinaryVoice(), true);
      // chat 窗口崩溃：不再心跳也没有 goodbye；过 TTL 后麦克风必须恢复，不能永久锁死。
      clock.now += 16000;
      pet.heartbeat(); flush();
      assert.equal(pet.runtime.blocksOrdinaryVoice(), false);
      assert.equal(await micReachesCapture(pet), true);
    """),
    ("theater_start_stops_mic_already_open_in_pet", r"""
      // The Pet mic was opened before the theater: blocking new starts is not
      // enough, the theater window's start broadcast must stop the live one.
      const { pet } = setupPair();
      const stops = [];
      pet.window.appAudioCapture.stopMicCapture = async () => { stops.push('stop'); pet.appState.isRecording = false; };
      const theaterWindow = new FakeBroadcastChannel('neko_page_channel');
      const transport = pet.window.nekoTheaterTransport;
      // A forged message without the theater schema is ignored.
      theaterWindow.postMessage({ action: 'theater:ordinary-voice-stop' });
      pet.appState.isRecording = true;
      flush(); await new Promise(resolve => setImmediate(resolve));
      assert.deepEqual(stops, []);
      // An idle Pet has nothing to stop.
      pet.appState.isRecording = false;
      theaterWindow.postMessage(transport.createMessage('theater-runtime', { action: 'theater:ordinary-voice-stop' }));
      flush(); await new Promise(resolve => setImmediate(resolve));
      assert.deepEqual(stops, []);
      pet.appState.isRecording = true;
      theaterWindow.postMessage(transport.createMessage('theater-runtime', { action: 'theater:ordinary-voice-stop' }));
      flush(); await new Promise(resolve => setImmediate(resolve));
      assert.deepEqual(stops, ['stop'], 'the Pet must stop the ordinary mic it opened before the theater');
      assert.equal(pet.appState.isRecording, false);
    """),
    ("ordinary_voice_stop_only_reaches_the_theater_character", r"""
      // /{lanlan_name} pages for different characters share the page channel; a
      // theater for one character must not stop another character's voice chat.
      const { pet } = setupPair();
      pet.window.lanlan_config = { lanlan_name: 'Mochi' };
      const stops = [];
      pet.window.appAudioCapture.stopMicCapture = async () => { stops.push('stop'); pet.appState.isRecording = false; };
      const theaterWindow = new FakeBroadcastChannel('neko_page_channel');
      const transport = pet.window.nekoTheaterTransport;
      pet.appState.isRecording = true;
      theaterWindow.postMessage(transport.createMessage('theater-runtime', { action: 'theater:ordinary-voice-stop', catgirl_name: 'Lanlan' }));
      flush(); await new Promise(resolve => setImmediate(resolve));
      assert.deepEqual(stops, [], 'a window for another character keeps recording');
      assert.equal(pet.appState.isRecording, true);
      theaterWindow.postMessage(transport.createMessage('theater-runtime', { action: 'theater:ordinary-voice-stop', catgirl_name: 'Mochi' }));
      flush(); await new Promise(resolve => setImmediate(resolve));
      assert.deepEqual(stops, ['stop'], 'a window for the theater character stops its ordinary voice');
      assert.equal(pet.appState.isRecording, false);
    """),
    ("closed_theater_window_goodbye_releases_mic", r"""
      const { pet, chat } = setupPair();
      setChatTheater(chat, true);
      assert.equal(pet.runtime.blocksOrdinaryVoice(), true);
      chat.listeners.beforeunload(); flush();
      assert.equal(pet.runtime.blocksOrdinaryVoice(), false, '剧场窗口正常关闭后应立即解除');
    """),
)


def _run(script: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script += "\nrun().then(() => process.stdout.write('ok')).catch(error => { console.error(error); process.exitCode = 1; });"
    result = run_node_stdin(node, script, cwd=str(ROOT), capture_output=True, check=False, timeout=10)
    assert result.returncode == 0, f"Node regression failed:\n{result.stdout}\n{result.stderr}"
    assert result.stdout == "ok", "async scenario did not complete"


@pytest.mark.parametrize("_name,scenario", SCENARIOS, ids=[case[0] for case in SCENARIOS])
def test_ordinary_voice_blocked_by_theater_in_other_window(_name, scenario):
    _run(HARNESS + "\nasync function run() {\n" + scenario + "\n}\n")


def test_floating_mic_click_uses_cross_window_guard_and_explains_it():
    # 悬浮麦克风的点击入口挂在整页 DOM 初始化里，无法单独装入 vm；这里固定它与
    # startMicCapture 共用同一个跨窗口判定，并在拦截时给出可见提示。
    source = (ROOT / "static/app/app-buttons.js").read_text(encoding="utf-8")
    start = source.index("micButton.addEventListener('click', async function () {")
    guard = source[start:source.index("micButton.classList.add('active');", start)]
    assert "window.nekoTheaterRuntime.blocksOrdinaryVoice()" in guard
    assert "getState().active" not in guard
    assert "theater.voiceUnavailable" in guard
