"""A running theater must not drive the ordinary GalGame option pipeline.

The theater runtime re-renders on every typewriter tick. It used to call
``reactChatWindowHost.openWindow()`` from each of those renders, and the host's
``openWindow`` re-requests ``/api/galgame/options`` (one summary-model call)
whenever GalGame mode is on, which is the default. These scenarios run the real
frontend modules inside a node ``vm`` context and pin the contract:

* the runtime claims the chat window once per theater session, not per render;
* the host never requests ordinary GalGame options while a theater presentation
  is active, and drops any request already in flight when a theater starts.
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
function createRuntime() {
  const requests = [], listeners = {}, callbacks = {};
  let openCount = 0, renderCount = 0;
  const host = {
    getState: () => ({}), getChatSurfaceMode: () => 'compact',
    setChatSurfaceMode() {}, setComposerHidden() {}, setGoodbyeComposerHidden() {},
    openWindow() { openCount += 1; },
    setViewProps() { renderCount += 1; },
    setOnTheaterSubmit: fn => { callbacks.freeform = fn; },
    setOnTheaterSuggestedInputSelect() {}, setOnTheaterEnd() {},
  };
  const window = {
    console: silentConsole, location: { origin: 'https://local.test' }, reactChatWindowHost: host,
    t: key => key, appState: {},
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    addEventListener: (name, fn) => { listeners[name] = fn; }, dispatchEvent() {}, confirm: () => true,
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: { readyState: 'loading', addEventListener() {}, querySelector: () => null,
      body: { classList: { contains: () => false } } },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    fetch: (url, opts) => new Promise((resolve, reject) => requests.push({ url, options: opts, resolve, reject })),
  };
  window.window = window;
  vm.createContext(window);
  for (const path of ['static/js/theater_transport.js', 'static/app/app-theater-runtime.js']) {
    vm.runInContext(fs.readFileSync(path, 'utf8'), window, { filename: path });
  }
  return {
    window, requests, listeners, callbacks, runtime: window.nekoTheaterRuntime,
    get openCount() { return openCount; }, get renderCount() { return renderCount; },
  };
}
function snapshot(sessionId) {
  return { ok: true, session: { story_package_id: 'story_' + sessionId, session_id: sessionId,
    revision: 4, status: 'active', opening_performance: { performance: '开场。' }, performance_history: [] },
    suggested_inputs: ['观察桌面。'], participants: { player_name: '玩家', catgirl_name: '猫娘' } };
}
async function respond(request, data, status = 200) {
  assert.ok(request, '应当发出对应请求');
  request.resolve({ status, ok: status < 400, json: async () => data });
  await tick(); await tick();
}
async function launch(ctx, sessionId) {
  ctx.listeners.message({ origin: 'https://local.test', data: {
    schema: ctx.window.nekoTheaterTransport.MESSAGE_SCHEMA, action: 'theater:launch-request',
    launch_id: 'launch_' + sessionId, story_id: 'story_' + sessionId,
    session_id: sessionId, revision: 4, launch_action: 'continue',
  } });
  await respond(ctx.requests.shift(), snapshot(sessionId));
  const claimIndex = ctx.requests.findIndex(request => request.url.includes('/session/' + sessionId));
  assert.ok(claimIndex >= 0);
  const claim = ctx.requests.splice(claimIndex, 1)[0];
  assert.ok(claim.options.headers['X-Neko-Theater-Activity']);
  await respond(claim, { ...snapshot(sessionId), activity_claimed: true });
  for (let i = ctx.requests.length - 1; i >= 0; i -= 1) {
    if (ctx.requests[i].url.endsWith('/session/release')) {
      await respond(ctx.requests.splice(i, 1)[0], { ok: true });
    }
  }
  assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
}
"""

HOST_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function createHost() {
  const fetches = [];
  const window = { location: { pathname: '/chat' } };
  window.window = window;
  const context = vm.createContext({
    window, console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
    setTimeout: () => 1, clearTimeout() {},
    AbortController: class { constructor() { this.signal = { aborted: false }; } abort() { this.signal.aborted = true; } },
    fetch: (url, opts) => { fetches.push({ url, opts }); return new Promise(() => {}); },
    document: { querySelector: () => null, addEventListener() {} },
  });
  vm.runInContext(fs.readFileSync('static/app/app-react-chat-window/message-bundle-actions-and-prompts.js', 'utf8'),
    context, { filename: 'message-bundle-actions-and-prompts.js' });
  const I = window.__appReactChatWindowParts;
  I.state = {
    galgameModeEnabled: true, galgameOptions: [], galgameOptionsLoading: false, _galgameRequestSeq: 0,
    choicePrompt: null, chatSurfaceMode: 'compact', compactChatState: 'default', viewProps: {},
    messages: [
      { id: 'u1', role: 'user', blocks: [{ type: 'text', text: '晚饭吃什么' }] },
      { id: 'a1', role: 'assistant', blocks: [{ type: 'text', text: '吃鱼吧喵' }] },
    ],
  };
  I.GALGAME_HISTORY_LIMIT = 6;
  I.renderWindow = () => {};
  I.getCurrentUserName = () => 'You';
  I.ensureViewProps = () => I.state.viewProps || {};
  I.getCurrentChatSurfaceMode = () => I.state.chatSurfaceMode;
  I.getCurrentCompactChatState = () => I.state.compactChatState;
  I.coerceChatSurfaceModeForHost = mode => mode;
  I.resetCompactChatState = () => {};
  return { I, fetches };
}
"""

RUNTIME_SCENARIOS = (
    ("window_is_claimed_once_per_session", r"""
      const ctx = createRuntime();
      await launch(ctx, 'session_a');
      assert.equal(ctx.openCount, 1, '启动剧场时应打开一次聊天窗口');
      // 失败回执、重试、每个打字机字符都会重新渲染；这些渲染不能再次 openWindow。
      const rendersBefore = ctx.renderCount;
      ctx.callbacks.freeform('询问细节。'); await tick();
      await respond(ctx.requests.shift(), { ok: false, reason: 'numeric_v2_actor_failed' }, 502);
      ctx.callbacks.freeform('再问一次。'); await tick();
      await respond(ctx.requests.shift(), { ok: false, reason: 'numeric_v2_actor_failed' }, 502);
      assert.ok(ctx.renderCount - rendersBefore >= 4, '场景本身必须产生多次渲染');
      assert.equal(ctx.openCount, 1, '同一 Session 的后续渲染不得重复 openWindow');
      // 新 Session 需要重新声明窗口一次；退出后同样重新开始计数。
      await launch(ctx, 'session_b');
      assert.equal(ctx.openCount, 2, '切换到新 Session 时应重新打开一次窗口');
      ctx.runtime.clear('test_exit');
      await launch(ctx, 'session_c');
      assert.equal(ctx.openCount, 3, '退出后再次启动应重新打开一次窗口');
    """),
)

HOST_SCENARIOS = (
    ("theater_blocks_ordinary_galgame_requests", r"""
      const { I, fetches } = createHost();
      I.fetchGalgameOptionsForLatestTurn();
      assert.equal(fetches.length, 1, '普通聊天最新一轮是 assistant 时应请求选项（证明闸门之外的路径正常）');
      assert.equal(I.state.galgameOptionsLoading, true);
      const pending = fetches[0].opts.signal;
      const seqBefore = I.state._galgameRequestSeq;
      I.setViewProps({ theaterPresentation: { active: true, phase: 'loading' } });
      assert.equal(pending.aborted, true, '剧场激活时必须中止仍在进行的普通 Galgame 请求');
      assert.ok(I.state._galgameRequestSeq > seqBefore, '剧场激活必须使等待中的回调失效');
      assert.equal(I.state.galgameOptionsLoading, false);
      for (let i = 0; i < 5; i += 1) I.fetchGalgameOptionsForLatestTurn();
      I.fetchPendingIcebreakerGalgameHandoffOrLatest();
      assert.equal(fetches.length, 1, '剧场演绎期间不得请求普通 Galgame 选项');
      I.setViewProps({ theaterPresentation: { active: false, phase: 'inactive' } });
      I.fetchGalgameOptionsForLatestTurn();
      assert.equal(fetches.length, 2, '剧场结束后普通 Galgame 选项应恢复');
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
def test_theater_runtime_claims_chat_window_once(_name, scenario):
    _run(RUNTIME_HARNESS + "\nasync function run() {\n" + scenario + "\n}\n")


@pytest.mark.parametrize("_name,scenario", HOST_SCENARIOS, ids=[case[0] for case in HOST_SCENARIOS])
def test_chat_host_skips_galgame_options_during_theater(_name, scenario):
    _run(HOST_HARNESS + "\nasync function run() {\n" + scenario + "\n}\n")
