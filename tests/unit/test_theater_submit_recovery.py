"""Run the real theater frontend module to verify submit recovery, with every request handled by an in-memory fake fetch."""

from pathlib import Path
import shutil

import pytest

from tests.node_harness import run_node_stdin


ROOT = Path(__file__).resolve().parents[2]
HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const copy = value => JSON.parse(JSON.stringify(value));
function createContext() {
  const requests = [], listeners = {}, views = [], callbacks = {};
  const host = {
    getState: () => ({}), getChatSurfaceMode: () => 'compact',
    setChatSurfaceMode() {}, setComposerHidden() {}, setGoodbyeComposerHidden() {}, openWindow() {},
    setViewProps: value => views.push(copy(value)),
    setOnTheaterSubmit: fn => { callbacks.freeform = fn; },
    setOnTheaterSuggestedInputSelect: fn => { callbacks.suggestion = fn; }, setOnTheaterEnd() {},
  };
  const window = {
    console, location: { origin: 'https://local.test' }, reactChatWindowHost: host,
    t: key => 'translated:' + key,
    // 不启动真实计时、音频或浏览器窗口；请求仅由测试显式完成。
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    addEventListener: (name, fn) => { listeners[name] = fn; }, dispatchEvent() {}, confirm: () => true,
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: { readyState: 'loading', addEventListener() {}, querySelector: () => null,
      body: { classList: { contains: () => false } } },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })),
  };
  window.window = window;
  vm.createContext(window);
  for (const path of ['static/js/theater_transport.js', 'static/app/app-theater-runtime.js']) {
    vm.runInContext(fs.readFileSync(path, 'utf8'), window, { filename: path });
  }
  return { window, requests, listeners, views, callbacks, runtime: window.nekoTheaterRuntime };
}
function snapshot(sessionId = 'session_a', revision = 4, status = 'active') {
  return { ok: true, session: { story_package_id: 'story_' + sessionId, session_id: sessionId,
    revision, status, opening_performance: { performance: '已提交开场。' }, performance_history: [] },
    suggested_inputs: status === 'ended' ? [] : ['观察桌面。', '询问细节。'],
    participants: { player_name: '玩家', catgirl_name: '猫娘' } };
}
async function respond(request, data, status = 200) {
  assert.ok(request, '应当发出对应请求');
  request.resolve({ status, ok: status < 400, json: async () => data });
  await tick(); await tick();
}
async function reject(request) { request.reject(new Error('模拟断网')); await tick(); await tick(); }
async function launch(ctx, sessionId = 'session_a', revision = 4) {
  ctx.listeners.message({ origin: 'https://local.test', data: {
    schema: ctx.window.nekoTheaterTransport.MESSAGE_SCHEMA, action: 'theater:launch-request',
    launch_id: 'launch_' + sessionId, story_id: 'story_' + sessionId,
    session_id: sessionId, revision, launch_action: 'continue',
  } });
  const request = ctx.requests.shift();
  assert.match(request.url, /\/session\//);
  await respond(request, snapshot(sessionId, revision));
  assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
}
async function submit(ctx, source = 'freeform', text = '询问细节。') {
  ctx.callbacks[source](text); await tick();
  const request = ctx.requests.shift();
  assert.equal(request.url, '/api/theater-numeric/session/input');
  assert.equal(JSON.parse(request.options.body).input_source, source);
  assert.equal(ctx.runtime.getState().phase, 'evaluating');
  return request;
}
function assertRecovery(ctx, before, buttons, draft) {
  const state = copy(ctx.runtime.getState());
  assert.equal(state.phase, 'awaiting_player');
  assert.equal(state.revision, before.revision);
  assert.deepEqual(state.history, before.history, '不能留下未提交玩家气泡');
  assert.deepEqual(state.suggestedInputs, buttons, '选项恢复必须遵守服务端提交状态');
  assert.equal(state.draftRestore && state.draftRestore.text || '', draft);
  assert.ok(state.errorMessage.trim(), '失败必须显示可见提示');
  assert.equal(ctx.views.at(-1).composerDisabled, false);
}
"""

SCENARIOS = (
    # 失败请求和缺报必须展示，幂等重放清零，新会话不能沿用上一份用量。
    ("usage_failure_replay_and_new_session", r"""
      const ctx = createContext(); await launch(ctx);
      ctx.window.t = key => key;
      const committedHistory = copy(ctx.runtime.getState().history);
      const request = await submit(ctx);
      await respond(request, { ok: false, reason: 'numeric_v2_actor_failed', token_usage: {
        input_tokens: 123, output_tokens: 7, complete: false,
        // 历史查找必须单列，不能归入 actor；总量仍以服务端返回值为准。
        calls: [{stage:'actor',input_tokens:83,output_tokens:3}, {stage:'history_lookup',input_tokens:40,output_tokens:4},
                {stage:'dispute',input_tokens:null,output_tokens:null}]
      }}, 502);
      let usage = ctx.views.at(-1).theaterPresentation.tokenUsage;
      assert.match(usage.summary, /123/); assert.match(usage.summary, /Partial/);
      assert.match(usage.detail, /\?/); assert.deepEqual(copy(ctx.runtime.getState().history), committedHistory);
      assert.match(usage.detail, /history_lookup/);
      const retry = await submit(ctx);
      await respond(retry, {...snapshot(), idempotent_replay:true, token_usage:{input_tokens:0,output_tokens:0,complete:true,calls:[]}});
      usage = ctx.views.at(-1).theaterPresentation.tokenUsage;
      assert.match(usage.summary, /0 calls/);
      await launch(ctx, 'session_b', 7);
      assert.equal(ctx.views.at(-1).theaterPresentation.tokenUsage, null);
    """),
    ("new_launch_clears_previous_submission_error", r"""
      const ctx = createContext(); await launch(ctx);
      const request = await submit(ctx, 'suggestion');
      await respond(request, { ok: false, reason: 'numeric_v2_actor_failed' }, 502);
      assert.ok(ctx.runtime.getState().errorMessage.trim());
      await launch(ctx, 'session_b', 7);
      assert.equal(ctx.runtime.getState().errorMessage, '');
      assert.equal(ctx.runtime.getState().pendingTurn, null);
    """),
    ("known_suggestion_failure", r"""
      for (const component of ['actor', 'evaluator']) {
        for (const failure of ['failed', 'unavailable']) {
          const ctx = createContext(); await launch(ctx);
          const before = copy(ctx.runtime.getState());
          const request = await submit(ctx, 'suggestion');
          await respond(request, { ok: false, reason: 'numeric_v2_' + component + '_' + failure }, failure === 'failed' ? 502 : 503);
          assertRecovery(ctx, before, before.suggestedInputs, '');
          assert.equal(ctx.runtime.getState().pendingTurn.id, JSON.parse(request.options.body).client_turn_id);
        }
      }
    """),
    ("known_freeform_failure", r"""
      const ctx = createContext(); await launch(ctx);
      const before = copy(ctx.runtime.getState());
      const request = await submit(ctx, 'freeform', '先聊聊你现在的感受。');
      await respond(request, { ok: false, reason: 'numeric_v2_actor_unavailable' }, 503);
      assertRecovery(ctx, before, before.suggestedInputs, '先聊聊你现在的感受。');
    """),
    ("uncertain_failure_keeps_retry_input_only", r"""
      for (const kind of ['network', 'unknown_http']) {
        const ctx = createContext(); await launch(ctx);
        const before = copy(ctx.runtime.getState());
        const request = await submit(ctx, 'suggestion');
        if (kind === 'network') await reject(request);
        // 内部旧诊断不是当前 HTTP 白名单，不能当成已确认未提交来恢复按钮。
        else await respond(request, { ok: false, reason: 'numeric_v2_actor_fact_boundary' }, 400);
        assertRecovery(ctx, before, [], '询问细节。');
        assert.equal(ctx.runtime.getState().pendingTurn.id, JSON.parse(request.options.body).client_turn_id);
      }
    """),
    ("retry_reuses_turn_id_and_commits_once", r"""
      const ctx = createContext(); await launch(ctx);
      const request = await submit(ctx); await reject(request);
      const retry = await submit(ctx);
      assert.equal(JSON.parse(retry.options.body).client_turn_id, JSON.parse(request.options.body).client_turn_id);
      assert.equal(JSON.parse(retry.options.body).base_revision, 4);
      const result = snapshot('session_a', 5);
      result.idempotent_replay = true;
      result.session.performance_history = [{ revision: 5, input_text: '询问细节。', performance: '这是细节。' }];
      result.suggested_inputs = ['新的选项。'];
      await respond(retry, result);
      const state = copy(ctx.runtime.getState());
      assert.equal(state.revision, 5); assert.equal(state.phase, 'awaiting_player');
      assert.equal(state.pendingTurn, null); assert.equal(state.draftRestore, null);
      assert.equal(state.history.filter(entry => entry.type === 'player_action').length, 1);
      assert.ok(state.history.some(entry => entry.text === '这是细节。'));
      assert.deepEqual(state.suggestedInputs, ['新的选项。']);
      assert.equal(ctx.requests.length, 0, '幂等回放不得重复请求 TTS 或提交');
    """),
    ("conflict_refresh_uses_authoritative_choices", r"""
      for (const reason of ['numeric_base_revision_mismatch', 'numeric_suggested_input_not_current', 'numeric_duplicate_client_turn_id']) {
        const ctx = createContext(); await launch(ctx);
        const request = await submit(ctx);
        await respond(request, { ok: false, reason }, 409);
        const refresh = ctx.requests.shift();
        assert.ok(refresh, reason + ' 必须刷新快照'); assert.match(refresh.url, /\/session\/session_a\?/);
        assert.equal(ctx.runtime.getState().phase, 'evaluating', '刷新完成前不得允许再次提交');
        assert.equal(ctx.views.at(-1).composerDisabled, true);
        const result = snapshot('session_a', 6); result.suggested_inputs = ['服务端新选项。'];
        result.session.performance_history = [{ revision: 6, input_text: '别处已提交。', performance: '最新状态。' }];
        await respond(refresh, result);
        const state = copy(ctx.runtime.getState());
        assert.equal(state.revision, 6); assert.equal(state.phase, 'awaiting_player');
        assert.deepEqual(state.suggestedInputs, ['服务端新选项。']);
        assert.ok(state.history.some(entry => entry.text === '最新状态。'));
        assert.ok(!state.history.some(entry => entry.id.startsWith('player-pending-')));
        const next = await submit(ctx, 'suggestion', '服务端新选项。');
        assert.equal(JSON.parse(next.options.body).base_revision, 6);
        assert.notEqual(JSON.parse(next.options.body).client_turn_id, JSON.parse(request.options.body).client_turn_id);
      }
    """),
    ("refresh_ended_or_failed_never_revives_old_choices", r"""
      for (const ended of [true, false]) {
        const ctx = createContext(); await launch(ctx);
        const request = await submit(ctx);
        await respond(request, { ok: false, reason: ended ? 'session_already_ended' : 'numeric_base_revision_mismatch' }, 409);
        const refresh = ctx.requests.shift(); assert.ok(refresh, '结束或冲突需要权威快照');
        if (ended) {
          const endedSnapshot = snapshot('session_a', 6, 'ended');
          endedSnapshot.end_receipt_id = 'receipt_ended_a';
          endedSnapshot.archive_request_id = 'archive_ended_a';
          await respond(refresh, endedSnapshot);
          assert.equal(ctx.runtime.getState().phase, 'ended');
          assert.equal(ctx.views.at(-1).composerDisabled, true);
          assert.equal(ctx.runtime.getState().pendingEnd.end_receipt_id, 'receipt_ended_a');
          assert.equal(ctx.runtime.getState().pendingEnd.archive_request_id, 'archive_ended_a');
          assert.equal(ctx.runtime.getState().errorMessage, 'translated:theater.ended');
          ctx.callbacks.freeform('不可继续。'); await tick();
          assert.equal(ctx.requests.length, 0, '已结束后不得再次提交');
        } else {
          await reject(refresh);
          assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
          assert.ok(ctx.runtime.getState().errorMessage.trim());
        }
        assert.deepEqual(copy(ctx.runtime.getState().suggestedInputs), []);
      }
    """),
    ("late_failure_cannot_restore_cleared_or_ending_session", r"""
      for (const ending of [false, true]) {
        const ctx = createContext(); await launch(ctx);
        const request = await submit(ctx);
        if (ending) {
          // 通过公开结束入口进入 ending，故意保持结束请求等待，不注入私有状态。
          void ctx.runtime.requestEnd(); await tick(); await tick();
          assert.equal(ctx.runtime.getState().phase, 'ending');
        } else ctx.runtime.clear('test_exit');
        const before = copy(ctx.runtime.getState());
        await respond(request, { ok: false, reason: 'numeric_v2_actor_failed' }, 502);
        assert.deepEqual(copy(ctx.runtime.getState()), before, '迟到失败不能恢复旧草稿、按钮或阶段');
      }
    """),
    ("late_failure_cannot_pollute_new_session", r"""
      for (const duringRefresh of [false, true]) {
        const ctx = createContext(); await launch(ctx);
        let late = await submit(ctx);
        if (duringRefresh) {
          await respond(late, { ok: false, reason: 'numeric_base_revision_mismatch' }, 409);
          late = ctx.requests.shift(); assert.ok(late);
        }
        await launch(ctx, 'session_b', 7);
        const before = copy(ctx.runtime.getState());
        await reject(late);
        const after = copy(ctx.runtime.getState());
        // 无业务变化的重绘序号不是旧响应污染，不把表现层计数当状态语义。
        delete before.presentationSeq; delete after.presentationSeq;
        assert.deepEqual(after, before);
        assert.equal(after.sessionId, 'session_b'); assert.equal(after.revision, 7);
        assert.equal(after.errorMessage, '');
        assert.ok(after.draftRestore.id, '新 Session 必须投影一次草稿清空');
        assert.equal(after.draftRestore.text, '');
      }
    """),
)


@pytest.mark.parametrize("_name,scenario", SCENARIOS, ids=[case[0] for case in SCENARIOS])
def test_theater_submit_recovery(_name, scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = HARNESS + "\nasync function run() {\n" + scenario + "\n}\n"
    script += "run().then(() => process.stdout.write('ok')).catch(error => { console.error(error); process.exitCode = 1; });"
    result = run_node_stdin(node, script, cwd=str(ROOT), capture_output=True, check=False, timeout=10)
    assert result.returncode == 0, f"Node 行为回归失败：\n{result.stdout}\n{result.stderr}"
    assert result.stdout == "ok", "异步场景未执行完成"
