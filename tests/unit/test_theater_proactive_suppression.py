"""Theater sessions must pause proactive chat without rewriting the persisted user setting.

The theater runtime used to flip ``appState.proactiveChatEnabled`` to ``false``
for the length of a session.  ``saveSettings()`` persists and broadcasts that
same field, so any settings save during a theater session (subtitle/translate
toggles, avatar popup) wrote ``false`` to disk and a crash then left proactive
chat permanently off.  These scenarios run the real frontend modules inside a
node ``vm`` context and pin the replacement contract:

* the runtime answers ``suppressesProactiveChat()`` from in-memory session
  state and never touches ``proactiveChatEnabled``;
* ``app-proactive.js`` consults that answer in its scheduling/trigger gate and
  propagates it to the leader window over the existing leader heartbeat, so the
  leader (index.html) stops while the theater runs in chat.html, and resumes
  once the theater exits or its window stops heartbeating.
"""

from pathlib import Path
import shutil

import pytest

from tests.node_harness import run_node_stdin


ROOT = Path(__file__).resolve().parents[2]

RUNTIME_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const silentConsole = { log() {}, info() {}, debug() {}, warn() {}, error() {} };
function createRuntime(options = {}) {
  const requests = [], listeners = {}, refreshes = [];
  const host = {
    getState: () => ({}), getChatSurfaceMode: () => 'compact',
    setChatSurfaceMode() {}, setComposerHidden() {}, setGoodbyeComposerHidden() {}, openWindow() {},
    setViewProps() {}, setOnTheaterSubmit() {}, setOnTheaterSuggestedInputSelect() {}, setOnTheaterEnd() {},
  };
  const appState = Object.assign({ proactiveChatEnabled: true }, options.appState || {});
  const pointer = options.pointer ? JSON.stringify(options.pointer) : null;
  const window = {
    console: silentConsole, location: { origin: 'https://local.test' }, reactChatWindowHost: host,
    t: key => key, appState,
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    addEventListener: (name, fn) => { listeners[name] = fn; }, dispatchEvent() {}, confirm: () => true,
    sessionStorage: { getItem: () => pointer, setItem() {}, removeItem() {} },
    document: { readyState: options.pointer ? 'complete' : 'loading', addEventListener() {}, querySelector: () => null,
      body: { classList: { contains: () => false } } },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    fetch: (url, opts) => new Promise((resolve, reject) => requests.push({ url, options: opts, resolve, reject })),
  };
  // 代替 app-proactive.js：记录每次通知时运行态给出的抑制结论。
  window.appProactive = { refreshProactiveSuppression() {
    const theater = window.nekoTheaterRuntime;
    refreshes.push(typeof theater.suppressesProactiveChat === 'function' ? theater.suppressesProactiveChat() : 'missing');
  } };
  // Opt-in: record what the runtime broadcasts to other windows.
  const broadcasts = [];
  if (options.recordBroadcasts) {
    window.BroadcastChannel = class {
      addEventListener() {}
      postMessage(data) { broadcasts.push(JSON.parse(JSON.stringify(data))); }
      close() {}
    };
  }
  window.window = window;
  vm.createContext(window);
  for (const path of ['static/js/theater_transport.js', 'static/app/app-theater-runtime.js']) {
    vm.runInContext(fs.readFileSync(path, 'utf8'), window, { filename: path });
  }
  const runtime = window.nekoTheaterRuntime;
  return { window, appState, requests, listeners, refreshes, runtime, broadcasts };
}
function snapshot(sessionId = 'session_a', revision = 4) {
  return { ok: true, session: { story_package_id: 'story_' + sessionId, session_id: sessionId,
    revision, status: 'active', opening_performance: { performance: '开场。' }, performance_history: [] },
    suggested_inputs: ['观察桌面。'], participants: { player_name: '玩家', catgirl_name: '猫娘' } };
}
async function respond(request, data, status = 200) {
  assert.ok(request, '应当发出对应请求');
  request.resolve({ status, ok: status < 400, json: async () => data });
  await tick(); await tick();
}
function sendLaunch(ctx, sessionId = 'session_a', revision = 4) {
  ctx.listeners.message({ origin: 'https://local.test', data: {
    schema: ctx.window.nekoTheaterTransport.MESSAGE_SCHEMA, action: 'theater:launch-request',
    launch_id: 'launch_' + sessionId, story_id: 'story_' + sessionId,
    session_id: sessionId, revision, launch_action: 'continue',
  } });
}
async function launch(ctx, sessionId = 'session_a', revision = 4) {
  sendLaunch(ctx, sessionId, revision);
  await respond(ctx.requests.shift(), snapshot(sessionId, revision));
  const claim = ctx.requests.shift();
  assert.ok(claim && !/claim_activity=false/.test(claim.url));
  await respond(claim, snapshot(sessionId, revision));
  assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
}
"""

RUNTIME_SCENARIOS = (
    ("launch_suppresses_without_touching_user_setting", r"""
      for (const enabled of [true, false]) {
        const ctx = createRuntime({ appState: { proactiveChatEnabled: enabled } });
        await launch(ctx);
        // saveSettings 读取的正是这个字段；剧场期间任何一次保存都必须写回用户原值。
        assert.equal(ctx.appState.proactiveChatEnabled, enabled, '不得改写持久化的用户设置');
        assert.equal(typeof ctx.runtime.suppressesProactiveChat, 'function', '运行态必须提供内存中的抑制查询');
        assert.equal(ctx.runtime.suppressesProactiveChat(), true, '剧场期间必须抑制主动搭话');
        assert.equal(ctx.refreshes.at(-1), true, '抑制开始必须通知调度器');
        ctx.runtime.clear('test_exit');
        assert.equal(ctx.runtime.suppressesProactiveChat(), false, '退出后必须解除抑制');
        assert.equal(ctx.refreshes.at(-1), false, '解除必须通知调度器');
        assert.equal(ctx.appState.proactiveChatEnabled, enabled);
      }
    """),
    ("end_and_external_exit_lift_suppression", r"""
      for (const exit of ['external-end', 'story-deleted', 'catgirl-switched']) {
        const ctx = createRuntime(); await launch(ctx);
        assert.equal(ctx.runtime.suppressesProactiveChat(), true);
        const data = { schema: ctx.window.nekoTheaterTransport.MESSAGE_SCHEMA, story_id: 'story_session_a', session_id: 'session_a' };
        data.action = exit === 'catgirl-switched' ? 'catgirl_switched' : 'theater:' + exit;
        ctx.listeners.message({ origin: 'https://local.test', data });
        assert.equal(ctx.runtime.getState().active, false, exit + ' 必须退出剧场');
        assert.equal(ctx.runtime.suppressesProactiveChat(), false, exit + ' 后必须解除抑制');
        assert.equal(ctx.refreshes.at(-1), false);
        assert.equal(ctx.appState.proactiveChatEnabled, true);
      }
    """),
    ("aborted_launch_releases_claim", r"""
      // 普通语音无法停止时启动放弃；启动阶段先行登记的抑制必须一并释放。
      const ctx = createRuntime({ appState: { isRecording: true } });
      sendLaunch(ctx);
      await respond(ctx.requests.shift(), snapshot());
      await respond(ctx.requests.shift(), snapshot());
      assert.equal(ctx.runtime.getState().active, false);
      assert.equal(ctx.runtime.suppressesProactiveChat(), false);
      assert.ok(ctx.refreshes.includes(true), '启动阶段应先登记抑制');
      assert.equal(ctx.refreshes.at(-1), false);
      assert.equal(ctx.appState.proactiveChatEnabled, true);
    """),
    ("reload_restored_session_reapplies_suppression", r"""
      const ctx = createRuntime({ pointer: { story_id: 'story_session_a', session_id: 'session_a' } });
      const request = ctx.requests.shift();
      assert.match(request.url, /\/session\/session_a\?/);
      await respond(request, snapshot());
      assert.equal(ctx.runtime.getState().active, true, '刷新后应恢复剧场会话');
      assert.equal(ctx.runtime.suppressesProactiveChat(), true, '恢复的会话必须重新抑制主动搭话');
      assert.equal(ctx.refreshes.at(-1), true, '恢复后必须通知调度器（含其他窗口的 leader）');
      assert.equal(ctx.appState.proactiveChatEnabled, true);
      ctx.runtime.clear('test_exit');
      assert.equal(ctx.runtime.suppressesProactiveChat(), false);
      assert.equal(ctx.refreshes.at(-1), false);
    """),
    ("launch_and_restore_ask_other_windows_to_stop_ordinary_voice", r"""
      // stopOrdinaryVoiceInput only sees this window; the Electron Pet mic must be
      // told over the shared page channel, on launch and on a reload restore.
      const stops = ctx => ctx.broadcasts.filter(message => message.action === 'theater:ordinary-voice-stop');
      const ctx = createRuntime({ recordBroadcasts: true });
      sendLaunch(ctx);
      await respond(ctx.requests.shift(), snapshot());
      await respond(ctx.requests.shift(), snapshot());
      assert.equal(ctx.runtime.getState().active, true);
      const sent = stops(ctx);
      assert.equal(sent.length, 1, 'launch must ask other windows to stop their ordinary voice');
      assert.equal(sent[0].schema, ctx.window.nekoTheaterTransport.MESSAGE_SCHEMA);
      // Windows of other characters share the channel; the stop names the bound character.
      assert.equal(sent[0].catgirl_name, '猫娘');
      ctx.runtime.clear('test_exit');

      const restored = createRuntime({ recordBroadcasts: true, pointer: { story_id: 'story_session_a', session_id: 'session_a' } });
      await respond(restored.requests.shift(), snapshot());
      assert.equal(restored.runtime.getState().active, true);
      assert.equal(stops(restored).length, 1, 'a restored session must also stop ordinary voice elsewhere');
      assert.equal(stops(restored)[0].catgirl_name, '猫娘');
      restored.runtime.clear('test_exit');
    """),
    ("start_names_the_bound_character_in_the_ordinary_voice_stop", r"""
      // A fresh start broadcasts before the server binding is known, so it names this
      // window's character, then repeats the stop for the bound character if it differs.
      const stops = ctx => ctx.broadcasts.filter(message => message.action === 'theater:ordinary-voice-stop');
      for (const [ownName, expected] of [['猫娘', ['猫娘']], ['Other', ['Other', '猫娘']]]) {
        const ctx = createRuntime({ recordBroadcasts: true });
        ctx.window.lanlan_config = { lanlan_name: ownName };
        ctx.listeners.message({ origin: 'https://local.test', data: {
          schema: ctx.window.nekoTheaterTransport.MESSAGE_SCHEMA, action: 'theater:start-request',
          launch_id: 'launch_start', story_id: 'story_session_a', session_id: 'session_a', character_id: 'char_a',
        } });
        await tick(); await tick();
        assert.deepEqual(stops(ctx).map(message => message.catgirl_name), [ownName]);
        await respond(ctx.requests.shift(), Object.assign(snapshot(), { resumed: true }));
        assert.equal(ctx.runtime.getState().active, true);
        assert.deepEqual(stops(ctx).map(message => message.catgirl_name), expected);
        ctx.runtime.clear('test_exit');
      }
    """),
    ("exit_releases_server_theater_activity_only_when_active", r"""
      // 服务端兜底按最近剧场请求计时；退出未结束的演绎必须显式释放，未激活的 clear 不得发请求。
      const ctx = createRuntime(); await launch(ctx);
      assert.equal(ctx.requests.length, 0);
      ctx.runtime.clear('test_exit');
      for (let i = 0; i < 5; i += 1) await tick();
      const release = ctx.requests.shift();
      assert.ok(release && /\/api\/theater-numeric\/session\/release$/.test(release.url), '退出必须释放服务端剧场信号');
      assert.equal(release.options.method, 'POST');
      // 只释放本窗口演绎的角色（服务端按响应里的原始猫娘名登记），不得清掉其他角色的兜底。
      assert.equal(JSON.parse(release.options.body).catgirl_name, '猫娘');
      assert.match(JSON.parse(release.options.body).activity_claim_id, /^theater_activity_/);
      await respond(release, { ok: true });
      ctx.runtime.clear('again');
      for (let i = 0; i < 5; i += 1) await tick();
      assert.equal(ctx.requests.length, 0, '未激活时 clear 不得请求服务端');
    """),
)

PROACTIVE_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app/app-proactive.js', 'utf8');
const clock = { now: 1_000_000 };
const channels = [];
const queue = [];
class FakeBroadcastChannel {
  constructor(name) { this.name = name; this.onmessage = null; channels.push(this); }
  postMessage(data) {
    const copy = JSON.parse(JSON.stringify(data));
    for (const peer of channels) {
      if (peer !== this && peer.name === this.name) queue.push(() => peer.onmessage && peer.onmessage({ data: copy }));
    }
  }
  close() {}
}
// BroadcastChannel 是异步投递；测试显式冲刷，模拟跨窗口消息到达。
function flush() { while (queue.length) queue.shift()(); }
function createWindow(pathname) {
  const timers = new Map(), intervals = [], fetches = [];
  let nextTimer = 1;
  const listeners = {};
  const appState = {
    proactiveChatEnabled: true, proactiveNewsChatEnabled: true, proactiveChatInterval: 60,
    proactiveChatBackoffLevel: 0, proactiveChatTimer: null, isRecording: false,
    _proactiveStartupDelayApplied: true,
  };
  const theater = { suppressed: false };
  const window = {
    console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
    location: { pathname, origin: 'https://local.test' },
    appState, appConst: {},
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      addEventListener() {}, getElementById: () => null, querySelector: () => null, activeElement: null,
      hasFocus: () => true, body: { classList: { contains: () => false }, getAttribute: () => null },
    },
    navigator: { userAgent: 'node' },
    addEventListener: (name, fn) => { listeners[name] = fn; },
    setTimeout: (fn, delay) => { const id = nextTimer++; timers.set(id, { fn, delay }); return id; },
    clearTimeout: id => { timers.delete(id); },
    setInterval: fn => { intervals.push(fn); return 0; },
    clearInterval() {},
    fetch: (url, opts) => { fetches.push({ url, opts }); return Promise.reject(new Error('offline')); },
    BroadcastChannel: FakeBroadcastChannel,
    nekoTheaterRuntime: { suppressesProactiveChat: () => theater.suppressed },
    lastUserInputTime: 0,
  };
  window.window = window;
  vm.createContext(window);
  window.__clock = clock;
  vm.runInContext('Date.now = () => globalThis.__clock.now;', window);
  vm.runInContext(source, window, { filename: 'static/app/app-proactive.js' });
  const proactive = window.appProactive;
  return {
    window, appState, theater, proactive, timers, intervals, fetches, listeners,
    // 心跳/过期检查由 setInterval 驱动；测试逐拍执行。
    heartbeat() { for (const fn of intervals) fn(); },
    scheduledDelay() {
      const timer = appState.proactiveChatTimer && timers.get(appState.proactiveChatTimer);
      return timer ? timer.delay : null;
    },
  };
}
function setupPair() {
  channels.length = 0; queue.length = 0;
  const pet = createWindow('/');
  const chat = createWindow('/chat');
  flush();
  assert.equal(pet.proactive.isProactiveLeader(), true, 'Pet 主窗口是 leader');
  assert.equal(chat.proactive.isProactiveLeader(), false, 'chat.html 是 follower');
  pet.proactive.scheduleProactiveChat();
  assert.ok(pet.scheduledDelay() !== null, '未抑制时 leader 应排定主动搭话');
  assert.equal(pet.proactive.canTriggerProactively(), true);
  return { pet, chat };
}
function suppressFromChat(chat, value) {
  chat.theater.suppressed = value;
  chat.proactive.refreshProactiveSuppression();
  flush();
}
"""

PROACTIVE_SCENARIOS = (
    ("local_suppression_gates_scheduler", r"""
      channels.length = 0; queue.length = 0;
      const pet = createWindow('/');
      pet.proactive.scheduleProactiveChat();
      assert.ok(pet.scheduledDelay() !== null);
      pet.theater.suppressed = true;
      pet.proactive.refreshProactiveSuppression();
      assert.equal(pet.appState.proactiveChatTimer, null, '抑制开始必须停掉已排定的计时器');
      assert.equal(pet.proactive.canTriggerProactively(), false);
      pet.proactive.scheduleProactiveChat();
      assert.equal(pet.appState.proactiveChatTimer, null, '抑制期间不得重新排定');
      assert.equal(pet.appState.proactiveChatEnabled, true, '不得改写用户设置');
      pet.theater.suppressed = false;
      pet.proactive.refreshProactiveSuppression();
      assert.ok(pet.scheduledDelay() !== null, '解除后必须恢复调度');
      assert.equal(pet.proactive.canTriggerProactively(), true);
    """),
    ("leader_follows_theater_in_other_window", r"""
      const { pet, chat } = setupPair();
      suppressFromChat(chat, true);
      assert.equal(pet.proactive.isProactiveChatSuppressed(), true, 'leader 必须感知 chat.html 中的剧场');
      assert.equal(pet.proactive.canTriggerProactively(), false);
      assert.equal(pet.appState.proactiveChatTimer, null, 'leader 的计时器必须停止');
      pet.proactive.scheduleProactiveChat();
      assert.equal(pet.appState.proactiveChatTimer, null);
      // 常规心跳持续携带抑制状态，不能被下一拍心跳冲掉。
      chat.heartbeat(); flush();
      assert.equal(pet.proactive.isProactiveChatSuppressed(), true);
      assert.equal(pet.appState.proactiveChatEnabled, true, 'leader 的用户设置保持不变');
      assert.equal(chat.appState.proactiveChatEnabled, true);
      suppressFromChat(chat, false);
      assert.equal(pet.proactive.isProactiveChatSuppressed(), false);
      assert.ok(pet.scheduledDelay() !== null, '剧场退出后 leader 必须恢复调度');
    """),
    ("crashed_theater_window_suppression_expires", r"""
      const { pet, chat } = setupPair();
      suppressFromChat(chat, true);
      assert.equal(pet.appState.proactiveChatTimer, null);
      // chat.html 崩溃：不再心跳也没有 goodbye；过 TTL 后 leader 必须自行恢复。
      clock.now += 16000;
      pet.heartbeat(); flush();
      assert.equal(pet.proactive.isProactiveChatSuppressed(), false, '过期对端的抑制不得残留');
      assert.ok(pet.scheduledDelay() !== null, '抑制过期后 leader 必须恢复调度');
    """),
    ("closed_theater_window_goodbye_lifts_suppression", r"""
      const { pet, chat } = setupPair();
      suppressFromChat(chat, true);
      assert.equal(pet.appState.proactiveChatTimer, null);
      // 剧场窗口正常关闭会经 beforeunload 广播 goodbye，leader 应立即恢复而不必等 TTL。
      chat.listeners.beforeunload(); flush();
      assert.equal(pet.proactive.isProactiveChatSuppressed(), false);
      assert.ok(pet.scheduledDelay() !== null, '剧场窗口关闭后 leader 必须恢复调度');
    """),
    ("trigger_skips_while_suppressed", r"""
      const { pet, chat } = setupPair();
      pet.appState.isRecording = true;
      suppressFromChat(chat, true);
      await pet.proactive.triggerProactiveChat();
      assert.equal(pet.fetches.length, 0, '抑制期间旧计时器触发也不得发请求');
      suppressFromChat(chat, false);
      await pet.proactive.triggerProactiveChat();
      assert.ok(pet.fetches.length > 0, '解除后同一路径应正常发请求（证明上面的断言不是空转）');
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


@pytest.mark.parametrize("_name,scenario", RUNTIME_SCENARIOS, ids=[case[0] for case in RUNTIME_SCENARIOS])
def test_theater_runtime_proactive_suppression(_name, scenario):
    _run(RUNTIME_HARNESS + "\nasync function run() {\n" + scenario + "\n}\n")


@pytest.mark.parametrize("_name,scenario", PROACTIVE_SCENARIOS, ids=[case[0] for case in PROACTIVE_SCENARIOS])
def test_proactive_scheduler_respects_theater_suppression(_name, scenario):
    _run(PROACTIVE_HARNESS + "\nasync function run() {\n" + scenario + "\n}\n")
