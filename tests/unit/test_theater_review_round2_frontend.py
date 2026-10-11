"""Theater capsule contracts from the PR #3162 round-2 review (frontend and cross-window).

These scenarios run the real ``app-theater-runtime.js`` (and, where noted, the
real chat-window host modules) inside node ``vm`` contexts and pin:

* the forced ``compact`` surface is a temporary override: it never reaches the
  persisted surface preference, and a reload restores the pre-theater mode
  recorded with the capsule pointer;
* exit restores composer visibility to the latest non-theater intent (goodbye
  or return during the performance) instead of replaying the entry snapshot,
  and entry announces the theater before un-hiding the composer;
* the theater composes with the home tutorial's input lock instead of
  overwriting it;
* a confirmed end survives an input turn that commits first (retry on the new
  revision), and the opening can be cancelled while it is generating without a
  late start result re-activating the capsule or the server guard;
* ordinary text / drop / avatar turns use the same local-or-peer theater check
  as ordinary voice;
* in Electron the only real audio socket lives in the Pet window, so the Pet
  window must accept the chat window's current theater speech correlations and
  relay playback boundaries back.
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
const copy = value => JSON.parse(JSON.stringify(value));
const silentConsole = { log() {}, info() {}, debug() {}, warn() {}, error() {} };
const channels = [];
const channelQueue = [];
class FakeBroadcastChannel {
  constructor(name) { this.name = name; this.listeners = []; channels.push(this); }
  addEventListener(type, fn) { if (type === 'message') this.listeners.push(fn); }
  postMessage(data) {
    const payload = copy(data);
    for (const peer of channels) {
      if (peer === this || peer.name !== this.name) continue;
      channelQueue.push(() => peer.listeners.forEach(fn => fn({ data: payload })));
    }
  }
  close() {}
}
function flush() { while (channelQueue.length) channelQueue.shift()(); }
function createContext(options = {}) {
  const requests = [], listeners = {}, views = [], callbacks = {}, calls = [], storage = {}, timers = [];
  const documentListeners = {};
  const clock = options.clock || null;
  if (options.pointer) storage['neko.theater.numeric.v2.capsule-pointer.v1'] = JSON.stringify(options.pointer);
  const hostState = Object.assign({ composerHiddenRequested: false, goodbyeComposerHidden: false }, options.hostState || {});
  let surfaceMode = options.surfaceMode || 'compact';
  let window;
  const host = {
    getState: () => Object.assign({}, hostState),
    getChatSurfaceMode: () => surfaceMode,
    setChatSurfaceMode: mode => { calls.push(['setChatSurfaceMode', mode]); surfaceMode = mode; },
    setComposerHidden: hidden => { calls.push(['setComposerHidden', hidden]); hostState.composerHiddenRequested = !!hidden; },
    // 与真实宿主一致：每次调用都留下来源记录。
    setGoodbyeComposerHidden: (hidden, reason) => {
      calls.push(['setGoodbyeComposerHidden', !!hidden, reason]);
      window.__nekoGoodbyeChatComposerHidden = { hidden: !!hidden, reason: reason || (hidden ? 'goodbye' : 'return'), timestamp: Date.now() };
      hostState.goodbyeComposerHidden = !!hidden;
    },
    openWindow() {},
    setViewProps: value => {
      calls.push(['setViewProps', !!(value.theaterPresentation && value.theaterPresentation.active)]);
      views.push(copy(value));
      if (Object.prototype.hasOwnProperty.call(value, 'chatSurfaceMode')) surfaceMode = value.chatSurfaceMode;
    },
    setOnTheaterSubmit: fn => { callbacks.freeform = fn; },
    setOnTheaterSuggestedInputSelect: fn => { callbacks.suggestion = fn; }, setOnTheaterEnd() {},
  };
  const selectorTarget = { closed: false, postMessage() {}, focus() {} };
  window = {
    console: silentConsole, location: { origin: 'https://local.test' }, reactChatWindowHost: host,
    t: key => key, appState: {},
    // 打字机的短间隔立即推进；语音与阅读兜底计时（>=1100ms）永不触发，只能由播放事件结束等待。
    // 长计时与周期计时只登记，由场景按假时钟显式触发。
    setTimeout: (fn, ms) => {
      if (Number(ms) < 1000) { setImmediate(fn); return 0; }
      timers.push({ kind: 'timeout', fn, ms: Number(ms), due: (clock ? clock.now : 0) + Number(ms), active: true });
      return timers.length;
    },
    clearTimeout: id => { if (timers[id - 1]) timers[id - 1].active = false; },
    setInterval: (fn, ms) => { timers.push({ kind: 'interval', fn, ms: Number(ms), active: true }); return timers.length; },
    clearInterval: id => { if (timers[id - 1]) timers[id - 1].active = false; },
    addEventListener: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); },
    removeEventListener: (name, fn) => { listeners[name] = (listeners[name] || []).filter(item => item !== fn); },
    dispatchEvent() {}, confirm: () => true,
    open: () => selectorTarget,
    sessionStorage: {
      getItem: key => Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : null,
      setItem: (key, value) => { storage[key] = String(value); },
      removeItem: key => { delete storage[key]; },
    },
    document: { readyState: options.pointer ? 'complete' : 'loading', visibilityState: 'visible',
      addEventListener: (name, fn) => { (documentListeners[name] = documentListeners[name] || []).push(fn); },
      querySelector: () => null, body: { classList: { contains: () => false } } },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    fetch: (url, opts) => new Promise((resolve, reject) => requests.push({ url, options: opts, resolve, reject })),
  };
  if (options.channel) window.BroadcastChannel = FakeBroadcastChannel;
  if (options.appProactive) window.appProactive = options.appProactive;
  if (clock) window.Date = class extends Date { static now() { return clock.now; } };
  window.window = window;
  vm.createContext(window);
  for (const path of ['static/js/theater_transport.js', 'static/app/app-theater-runtime.js']) {
    vm.runInContext(fs.readFileSync(path, 'utf8'), window, { filename: path });
  }
  function emit(name, detail) { (listeners[name] || []).slice().forEach(fn => fn({ type: name, detail })); }
  function fireDueTimeouts() {
    for (const timer of timers.slice()) {
      if (timer.kind === 'timeout' && timer.active && timer.due <= clock.now) { timer.active = false; timer.fn(); }
    }
  }
  const activeIntervals = () => timers.filter(timer => timer.kind === 'interval' && timer.active);
  function setVisibility(value) {
    window.document.visibilityState = value;
    (documentListeners.visibilitychange || []).slice().forEach(fn => fn({ type: 'visibilitychange' }));
  }
  return { window, requests, listeners, views, callbacks, calls, storage, hostState, emit, fireDueTimeouts, activeIntervals,
    setVisibility,
    runtime: window.nekoTheaterRuntime, get surfaceMode() { return surfaceMode; } };
}
function snapshot(sessionId = 'session_a', revision = 4, status = 'active') {
  return { ok: true, session: { story_package_id: 'story_' + sessionId, session_id: sessionId,
    revision, lifecycle_revision: 0, status, opening_performance: { performance: '开场。' }, performance_history: [] },
    suggested_inputs: status === 'ended' ? [] : ['观察桌面。'], participants: { player_name: '玩家', catgirl_name: '猫娘' } };
}
async function respond(request, data, status = 200) {
  assert.ok(request, '应当发出对应请求');
  request.resolve({ status, ok: status < 400, json: async () => data });
  for (let i = 0; i < 4; i += 1) await tick();
}
function take(ctx, pattern) {
  const index = ctx.requests.findIndex(request => pattern.test(request.url));
  assert.ok(index >= 0, '缺少请求 ' + pattern);
  return ctx.requests.splice(index, 1)[0];
}
function sendLaunch(ctx, sessionId = 'session_a', revision = 4, action = 'theater:launch-request') {
  ctx.listeners.message[0]({ origin: 'https://local.test', data: {
    schema: ctx.window.nekoTheaterTransport.MESSAGE_SCHEMA, action,
    launch_id: 'launch_' + sessionId + '_' + action, story_id: 'story_' + sessionId,
    session_id: sessionId, revision, launch_action: 'continue', character_id: 'cat',
  } });
}
async function launch(ctx, sessionId = 'session_a', revision = 4) {
  sendLaunch(ctx, sessionId, revision);
  await respond(take(ctx, /\/session\/session_/), snapshot(sessionId, revision));
  await claimLaunch(ctx, snapshot(sessionId, revision));
  assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
}
async function claimLaunch(ctx, data) {
  const claim = take(ctx, /\/session\/session_/);
  assert.ok(claim.options.headers['X-Neko-Theater-Activity']);
  assert.ok(!claim.url.includes('claim_activity=false'));
  await respond(claim, { ...data, activity_claimed: true });
}
async function submit(ctx, text = '询问细节。') {
  ctx.callbacks.freeform(text); await tick();
  const request = take(ctx, /\/session\/input$/);
  assert.equal(ctx.runtime.getState().phase, 'evaluating');
  return request;
}
"""

RUNTIME_SCENARIOS = (
    ("cancelled_opening_retires_before_second_start", r"""
      const ctx = createContext();
      sendLaunch(ctx, 'session_a', 0, 'theater:start-request');
      for (let i = 0; i < 4; i++) await tick();
      const first = take(ctx, /\/session\/start$/);
      await ctx.runtime.requestEnd();
      ctx.listeners.message[0]({origin: 'https://local.test', data: {
        schema: ctx.window.nekoTheaterTransport.MESSAGE_SCHEMA, action: 'theater:start-request',
        launch_id: 'second-start', story_id: 'story_session_a', session_id: 'session_a', character_id: 'cat'
      }});
      for (let i = 0; i < 4; i++) await tick();
      assert.equal(ctx.requests.filter(r => /\/session\/start$/.test(r.url)).length, 0);
      await respond(first, snapshot('session_a', 0));
      const cleanup = take(ctx, /\/session\/end$/);
      assert.equal(JSON.parse(cleanup.options.body).cancelled_start, true);
      assert.equal(ctx.requests.filter(r => /\/session\/start$/.test(r.url)).length, 0);
      await respond(cleanup, snapshot('session_a', 0, 'ended'));
      const second = take(ctx, /\/session\/start$/);
      const cancelled = snapshot('session_a', 0, 'ended');
      cancelled.session.ended_reason = 'cancelled_start';
      await respond(second, {...cancelled, resumed: true});
      const replacement = take(ctx, /\/session\/start$/);
      const payload = JSON.parse(replacement.options.body);
      assert.equal(payload.replace_existing, true);
      assert.notEqual(replacement.options.headers['X-Neko-Theater-Activity'], second.options.headers['X-Neko-Theater-Activity']);
      assert.notEqual(payload.session_id, 'session_a');
      assert.equal(ctx.runtime.getState().sessionId, payload.session_id);
      const fresh = snapshot(payload.session_id, 0);
      fresh.session.story_package_id = 'story_session_a';
      await respond(replacement, fresh);
      assert.equal(ctx.runtime.getState().sessionStatus, 'active');
      assert.notEqual(ctx.runtime.getState().phase, 'ended');
    """),
    ("m5_pointer_carries_pre_theater_surface_mode", r"""
      const first = createContext({ surfaceMode: 'full' });
      await launch(first);
      const pointer = JSON.parse(first.storage['neko.theater.numeric.v2.capsule-pointer.v1']);
      assert.equal(pointer.chat_surface_mode, 'full', '恢复指针必须记录进入剧场前的聊天形态');
      assert.equal(first.surfaceMode, 'compact');
      // 演绎中刷新：宿主此刻已是剧场覆盖出的 compact，恢复后退出必须回到 full。
      const reloaded = createContext({ surfaceMode: 'compact', pointer });
      await respond(take(reloaded, /\/session\/session_a\?/), snapshot());
      assert.equal(reloaded.runtime.getState().active, true);
      reloaded.runtime.clear('test_exit');
      assert.deepEqual(reloaded.calls.filter(call => call[0] === 'setChatSurfaceMode').at(-1), ['setChatSurfaceMode', 'full']);
      assert.equal(reloaded.storage['neko.theater.numeric.v2.capsule-pointer.v1'], undefined);
    """),
    ("l9_entry_announces_theater_before_unhiding_composer", r"""
      const ctx = createContext({ hostState: { goodbyeComposerHidden: true } });
      sendLaunch(ctx);
      await respond(take(ctx, /\/session\/session_a/), snapshot());
      await claimLaunch(ctx, snapshot());
      const firstUnhide = ctx.calls.findIndex(call => call[0] === 'setGoodbyeComposerHidden' && call[1] === false);
      const firstTheaterView = ctx.calls.findIndex(call => call[0] === 'setViewProps' && call[1] === true);
      assert.ok(firstUnhide >= 0 && firstTheaterView >= 0);
      assert.ok(firstTheaterView < firstUnhide, '剧场投影必须先于恢复输入区生效，否则 goodbye 模式会触发普通 Galgame 请求');
    """),
    ("l9_exit_keeps_goodbye_and_return_made_during_theater", r"""
      // 剧场期间“请她离开”：退出后输入区应保持隐藏。
      const leave = createContext();
      await launch(leave);
      leave.window.reactChatWindowHost.setGoodbyeComposerHidden(true, 'live2d-goodbye-click');
      const request = await submit(leave);   // 后续渲染会再次把输入区设为可见
      assert.equal(leave.hostState.goodbyeComposerHidden, false);
      leave.runtime.clear('test_exit');
      assert.equal(leave.hostState.goodbyeComposerHidden, true, '剧场期间的“请她离开”不能被进入时的快照覆盖');
      void request;
      // 以 goodbye 状态进入，剧场期间“回来”：退出后输入区应保持可见。
      const back = createContext({ hostState: { goodbyeComposerHidden: true } });
      await launch(back);
      back.window.reactChatWindowHost.setGoodbyeComposerHidden(false, 'return-complete');
      back.runtime.clear('test_exit');
      assert.equal(back.hostState.goodbyeComposerHidden, false, '剧场期间的“回来”不能被进入时的 goodbye 快照覆盖');
      // 剧场期间没有外部变化时仍恢复进入时的状态。
      const plain = createContext({ hostState: { goodbyeComposerHidden: true } });
      await launch(plain);
      assert.equal(plain.hostState.goodbyeComposerHidden, false);
      plain.runtime.clear('test_exit');
      assert.equal(plain.hostState.goodbyeComposerHidden, true);
    """),
    ("l10_theater_composes_with_external_input_lock", r"""
      const ctx = createContext({ hostState: { composerExternallyLocked: true } });
      await launch(ctx);
      const view = ctx.views.at(-1);
      assert.equal(view.composerDisabled, true, '等待输入阶段也不能解除首页教程的输入锁');
      assert.equal(view.compactChatState, 'default');
      ctx.runtime.clear('test_exit');
      assert.equal(ctx.views.at(-1).composerDisabled, true, '退出剧场不能解除外部输入锁');
      const unlocked = createContext();
      await launch(unlocked);
      assert.equal(unlocked.views.at(-1).composerDisabled, false);
      assert.equal(unlocked.views.at(-1).compactChatState, 'input');
    """),
    ("l11_confirmed_end_retries_after_input_commits_first", r"""
      for (const inputFirst of [true, false]) {
        const ctx = createContext();
        await launch(ctx);
        const input = await submit(ctx);
        void ctx.runtime.requestEnd(); for (let i = 0; i < 4; i += 1) await tick();
        const end = take(ctx, /\/session\/end$/);
        assert.equal(JSON.parse(end.options.body).base_revision, 4);
        const committed = { ...snapshot('session_a', 5), performance: { performance: '你好。' } };
        if (inputFirst) {
          await respond(input, committed);
          assert.equal(ctx.runtime.getState().phase, 'ending', '已确认结束时，先提交的回合不能重新开始演绎');
          assert.equal(ctx.requests.filter(r => /speak-block/.test(r.url)).length, 0);
        }
        await respond(end, { ok: false, reason: 'numeric_base_revision_mismatch' }, 409);
        await respond(take(ctx, /\/session\/session_a\?/), snapshot('session_a', 5));
        const retry = take(ctx, /\/session\/end$/);
        assert.equal(JSON.parse(retry.options.body).base_revision, 5, '结束必须按新 revision 重试');
        await respond(retry, { ...snapshot('session_a', 5, 'ended'), end_receipt_id: 'receipt_a' });
        assert.equal(ctx.runtime.getState().active, false, '确认的结束不能被静默丢弃');
        if (!inputFirst) {
          await respond(input, committed);
          assert.equal(ctx.runtime.getState().active, false, '迟到的回合结果不能重新接管');
        }
      }
    """),
    ("l11_relaunch_during_end_invalidates_stale_end_response", r"""
      for (const endOk of [true, false]) {
        const ctx = createContext();
        await launch(ctx);
        let endResult = null;
        ctx.runtime.requestEnd().then(value => { endResult = value; }); for (let i = 0; i < 4; i += 1) await tick();
        const end = take(ctx, /\/session\/end$/);
        assert.equal(ctx.runtime.getState().phase, 'ending');
        // 选剧页恢复并重新接管同一 Session：旧结束响应不能关闭它。
        const resumed = snapshot('session_a', 4); resumed.session.lifecycle_revision = 2;
        ctx.listeners.message[0]({ origin: 'https://local.test', data: {
          schema: ctx.window.nekoTheaterTransport.MESSAGE_SCHEMA, action: 'theater:launch-request',
          launch_id: 'resumed_' + endOk, story_id: 'story_session_a', session_id: 'session_a', revision: 4, launch_action: 'continue' } });
        await respond(take(ctx, /\/session\/session_a\?/), resumed);
        await claimLaunch(ctx, resumed);
        // Replacing the previous owner releases only its activity claim.
        const release = take(ctx, /\/session\/release$/);
        await respond(release, { ok: true });
        assert.equal(ctx.runtime.getState().lifecycleRevision, 2);
        await respond(end, endOk ? { ...snapshot('session_a', 4, 'ended'), end_receipt_id: 'stale' }
          : { ok: false, reason: 'numeric_base_lifecycle_revision_mismatch' }, endOk ? 200 : 409);
        assert.equal(endResult, false);
        assert.equal(ctx.runtime.getState().active, true);
        assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
        assert.equal(ctx.requests.filter(r => /\/session\//.test(r.url)).length, 0, '旧结束流程不能再刷新或重试');
      }
    """),
    ("l11_second_conflict_surfaces_visible_error", r"""
      const ctx = createContext();
      await launch(ctx);
      void ctx.runtime.requestEnd(); for (let i = 0; i < 4; i += 1) await tick();
      await respond(take(ctx, /\/session\/end$/), { ok: false, reason: 'numeric_base_revision_mismatch' }, 409);
      await respond(take(ctx, /\/session\/session_a\?/), snapshot('session_a', 6));
      await respond(take(ctx, /\/session\/end$/), { ok: false, reason: 'numeric_base_revision_mismatch' }, 409);
      assert.equal(ctx.requests.filter(r => /\/session\/end$/.test(r.url)).length, 0, '只重试一次');
      assert.equal(ctx.runtime.getState().phase, 'awaiting_player');
      assert.match(ctx.runtime.getState().errorMessage, /无法结束/);
    """),
    ("l12_opening_generation_can_be_cancelled", r"""
      const ctx = createContext();
      sendLaunch(ctx, 'session_a', 0, 'theater:start-request');
      for (let i = 0; i < 4; i += 1) await tick();
      const start = take(ctx, /\/session\/start$/);
      assert.equal(ctx.runtime.getState().phase, 'loading');
      assert.equal(await ctx.runtime.requestEnd(), true, '开场生成期间必须允许退出');
      assert.equal(ctx.runtime.getState().active, false);
      assert.equal(ctx.runtime.suppressesProactiveChat(), false);
      await respond(start, snapshot('session_a', 0));
      assert.equal(ctx.runtime.getState().active, false, '迟到的开场结果不能重新激活剧场');
      const release = take(ctx, /\/session\/release$/);
      const released = JSON.parse(release.options.body);
      assert.equal(released.activity_claim_id, start.options.headers['X-Neko-Theater-Activity'], '必须只释放本次开场的活动标识');
      assert.equal(ctx.requests.filter(r => /speak-block/.test(r.url)).length, 0);
    """),
    ("m6_ordinary_chat_uses_local_or_peer_theater_check", r"""
      const peer = { suppressed: false };
      const ctx = createContext({ appProactive: { refreshProactiveSuppression() {}, isProactiveSuppressedByPeer: () => peer.suppressed } });
      assert.equal(typeof ctx.runtime.blocksOrdinaryChat, 'function');
      assert.equal(ctx.runtime.blocksOrdinaryChat(), false, '从未使用剧场时不能拦截普通对话');
      peer.suppressed = true;
      assert.equal(ctx.runtime.blocksOrdinaryChat(), true, '其他窗口的剧场必须拦截本窗口的普通对话');
      peer.suppressed = false;
      await launch(ctx);
      assert.equal(ctx.runtime.blocksOrdinaryChat(), true);
      ctx.runtime.clear('test_exit');
      assert.equal(ctx.runtime.blocksOrdinaryChat(), false);
    """),
    ("electron_pet_plays_chat_window_theater_speech", r"""
      channels.length = 0; channelQueue.length = 0;
      const chat = createContext({ channel: true });
      const pet = createContext({ channel: true });
      let petQueueCleared = 0;
      pet.window.appAudioPlayback = { clearAudioQueueWithoutDecoderReset() { petQueueCleared += 1; } };
      await launch(chat); flush();
      const turn = await submit(chat);
      await respond(turn, { ...snapshot('session_a', 5), performance: { performance: '你好。' } });
      flush();
      const speak = take(chat, /speak-block/);
      const id = JSON.parse(speak.options.body).playback_request_id;
      assert.equal(pet.runtime.getState().active, false);
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), true, 'Pet 持有唯一真实音频通道，必须放行剧场窗口当前的对白');
      assert.equal(pet.runtime.allowsSpeechCorrelation('theater_speech_stale'), false);
      await respond(speak, { ok: true, speech_id: 'speech_1', audio_queued: true });
      assert.equal(chat.runtime.getState().phase, 'performing');
      // Pet 播放完成后把播放边界转回剧场窗口，正文按真实播放推进。
      pet.emit('neko-assistant-speech-end', { turnId: 'speech_1' }); flush();
      for (let i = 0; i < 6; i += 1) await tick();
      assert.equal(chat.runtime.getState().phase, 'awaiting_player');
      flush();
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), false, '播放结束后不再放行');
      // 退出时正在 Pet 播放的剧场对白必须被清掉，迟到音频被拒绝。
      const second = await submit(chat, '继续。');
      await respond(second, { ...snapshot('session_a', 6), performance: { performance: '好的。' } });
      flush();
      const secondId = JSON.parse(take(chat, /speak-block/).options.body).playback_request_id;
      pet.window.appState.currentPlayingSpeechCorrelationId = secondId;
      chat.runtime.clear('test_exit'); flush();
      assert.equal(pet.runtime.allowsSpeechCorrelation(secondId), false);
      assert.equal(petQueueCleared, 1, '剧场窗口作废对白时 Pet 的播放队列必须一并清掉');
    """),
    ("electron_pet_drops_speech_allowlist_of_a_dead_chat_window", r"""
      async function pendingLine() {
        channels.length = 0; channelQueue.length = 0;
        const clock = { now: 1000000 };
        const chat = createContext({ channel: true, clock });
        const pet = createContext({ channel: true, clock });
        const cleared = { count: 0 };
        pet.window.appAudioPlayback = { clearAudioQueueWithoutDecoderReset() { cleared.count += 1; } };
        await launch(chat); flush();
        const turn = await submit(chat);
        await respond(turn, { ...snapshot('session_a', 5), performance: { performance: '你好。' } });
        flush();
        const speak = take(chat, /speak-block/);
        const id = JSON.parse(speak.options.body).playback_request_id;
        assert.equal(pet.runtime.allowsSpeechCorrelation(id), true);
        pet.window.appState.currentPlayingSpeechCorrelationId = id;
        return { clock, chat, pet, cleared, speak, id };
      }
      // 剧场窗口存活：对白请求未结束期间持续续期，长句不会被截断。
      const alive = await pendingLine();
      const refresh = alive.chat.activeIntervals();
      assert.equal(refresh.length, 1, '有待播对白时剧场窗口必须周期续期放行表');
      for (let i = 0; i < 10; i += 1) {
        alive.clock.now += refresh[0].ms; refresh[0].fn(); flush(); alive.pet.fireDueTimeouts();
      }
      assert.equal(alive.pet.runtime.allowsSpeechCorrelation(alive.id), true, '续期期间长句不能被截断');
      assert.equal(alive.cleared.count, 0);
      await respond(alive.speak, { ok: true, speech_id: 'speech_1', audio_queued: true });
      alive.pet.emit('neko-assistant-speech-end', { turnId: 'speech_1' }); flush();
      for (let i = 0; i < 6; i += 1) await tick();
      flush();
      assert.equal(alive.chat.activeIntervals().length, 0, '对白结束后停止续期');
      assert.equal(alive.pet.runtime.allowsSpeechCorrelation(alive.id), false);

      // 剧场窗口崩溃：既不续期也不广播空表。Pet 在 TTL 后拒绝该对白之后到达的新音频头，
      // 但不清掉开始时仍有效、已在播放或排队的音频。
      const crashed = await pendingLine();
      crashed.clock.now += 7100; crashed.pet.fireDueTimeouts();
      assert.equal(crashed.pet.runtime.allowsSpeechCorrelation(crashed.id), false, '广播窗口失联后迟到的新音频头必须被拒绝');
      assert.equal(crashed.cleared.count, 0, '到期只拒绝新音频，不得截断已在播放的对白');

      // 剧场窗口关闭或重载：立即撤回放行表，不等待 TTL。
      const reloaded = await pendingLine();
      reloaded.chat.emit('pagehide'); flush();
      assert.equal(reloaded.pet.runtime.allowsSpeechCorrelation(reloaded.id), false, '页面卸载时必须立即撤回放行表');
      assert.equal(reloaded.cleared.count, 1);
    """),
    ("electron_pet_keeps_a_hidden_chat_windows_line_through_timer_throttling", r"""
      channels.length = 0; channelQueue.length = 0;
      const clock = { now: 1000000 };
      const chat = createContext({ channel: true, clock });
      const pet = createContext({ channel: true, clock });
      const cleared = { count: 0 };
      pet.window.appAudioPlayback = { clearAudioQueueWithoutDecoderReset() { cleared.count += 1; } };
      const allowlists = [];
      const petChannel = channels[channels.length - 1];
      petChannel.addEventListener('message', event => {
        if (event.data && event.data.action === 'theater:speech-allowlist') allowlists.push(event.data);
      });
      await launch(chat); flush();
      const turn = await submit(chat);
      await respond(turn, { ...snapshot('session_a', 5), performance: { performance: '你好。' } });
      flush();
      const speak = take(chat, /speak-block/);
      const id = JSON.parse(speak.options.body).playback_request_id;
      pet.window.appState.currentPlayingSpeechCorrelationId = id;
      // 窗口转入后台后计时器被强节流：2 s 的续期计时器每分钟才触发一次，长句不能在 Pet 被截断。
      const sent = allowlists.length;
      chat.setVisibility('hidden'); flush();
      const [refresh] = chat.activeIntervals();
      for (let i = 0; i < 5; i += 1) {
        clock.now += 60000; pet.fireDueTimeouts();
        assert.equal(pet.runtime.allowsSpeechCorrelation(id), true, '节流期间对白的新音频头仍须放行');
        refresh.fn(); flush();
      }
      assert.equal(cleared.count, 0, '节流期间不得清掉正在播放的对白');
      // 可见时用短 TTL；转入后台立即以覆盖强节流的长 TTL 重发，不等下一次续期。
      assert.equal(allowlists[sent - 1].ttl_ms, 7000, '可见窗口使用短 TTL');
      assert.equal(allowlists[sent].ttl_ms, 75000, '可见性变化必须立即以长 TTL 重发放行表');
      assert.equal(allowlists.at(-1).ttl_ms, 75000, '隐藏期间续期沿用长 TTL');

      // 回到前台：立即改回短 TTL；此后窗口崩溃，Pet 在短 TTL 后拒绝新的音频头。
      const beforeVisible = allowlists.length;
      chat.setVisibility('visible'); flush();
      assert.equal(allowlists.length, beforeVisible + 1);
      assert.equal(allowlists.at(-1).ttl_ms, 7000);
      clock.now += 7100; pet.fireDueTimeouts();
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), false, '恢复可见后崩溃，迟到的新音频头必须在短 TTL 后被拒绝');
      assert.equal(cleared.count, 0, '到期不清掉已在播放的对白');

      // 隐藏窗口崩溃：长 TTL 到期后同样拒绝新音频头。
      chat.setVisibility('hidden'); flush();
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), true);
      clock.now += 74900;
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), true);
      clock.now += 200;
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), false, '隐藏窗口崩溃后长 TTL 到期必须拒绝新音频头');

      // 收音窗口钳制 ttl_ms：超大值按上限，缺失或非法值按短 TTL。
      function deliver(ttl) {
        const data = pet.window.nekoTheaterTransport.createMessage('theater-runtime', {
          action: 'theater:speech-allowlist', runtime_instance: 'theater_runtime_other', request_ids: [id], ttl_ms: ttl,
        });
        pet.listeners.message.forEach(fn => fn({ origin: 'https://local.test', data }));
        assert.equal(pet.runtime.allowsSpeechCorrelation(id), true);
      }
      deliver(36e5);
      clock.now += 119900;
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), true);
      clock.now += 200;
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), false, 'ttl_ms 超过上限时按上限到期');
      deliver('bogus');
      clock.now += 6900;
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), true);
      clock.now += 200;
      assert.equal(pet.runtime.allowsSpeechCorrelation(id), false, '缺失或非法 ttl_ms 按短 TTL 到期');
      assert.equal(cleared.count, 0);
    """),
)

HOST_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function extract(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, 'missing ' + startMarker);
  return source.slice(start, end);
}
function createHost() {
  const persisted = [];
  const window = { location: { pathname: '/' } };
  window.window = window;
  const context = vm.createContext({
    window, console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
    setTimeout: () => 1, clearTimeout() {},
    AbortController: class { constructor() { this.signal = {}; } abort() {} },
    fetch: () => new Promise(() => {}),
    document: { querySelector: () => null, addEventListener() {} },
    localStorage: { setItem: (key, value) => persisted.push([key, value]), getItem: () => null },
    shouldPersistChatSurfaceModePreference: () => true,
    CHAT_SURFACE_MODE_STORAGE_KEY: 'neko.reactChatWindow.chatSurfaceMode',
  });
  vm.runInContext(fs.readFileSync('static/app/app-react-chat-window/message-bundle-actions-and-prompts.js', 'utf8'),
    context, { filename: 'message-bundle-actions-and-prompts.js' });
  const I = window.__appReactChatWindowParts;
  context.I = I;
  // 真实的偏好写入函数（bootstrap），在同一上下文中运行。
  const bootstrap = fs.readFileSync('static/app/app-react-chat-window/bootstrap-state-and-geometry.js', 'utf8');
  vm.runInContext(extract(bootstrap, 'I.persistChatSurfaceModePreference = function', '\n    I.readGalgameModePreference'), context);
  I.state = { chatSurfaceMode: 'full', compactChatState: 'default', viewProps: {}, messages: [],
    galgameOptions: [], galgameOptionsLoading: false, _galgameRequestSeq: 0, composerHidden: false,
    goodbyeComposerHidden: false, composerAttachments: [], homeTutorialInteractionLocked: false, homeTutorialInputLocked: false };
  I.renderWindow = () => {};
  I.ensureViewProps = () => I.state.viewProps || {};
  I.getCurrentChatSurfaceMode = () => I.state.chatSurfaceMode;
  I.getCurrentCompactChatState = () => I.state.compactChatState;
  I.normalizeCompactChatState = mode => mode;
  I.coerceChatSurfaceModeForHost = mode => mode;
  I.resetCompactChatState = () => {};
  I.clearCompactMinimizePressTimer = () => {};
  I.syncChatSurfaceModeUI = () => {};
  I.getEffectiveComposerHidden = () => false;
  I.cloneMessage = message => message;
  return { I, persisted };
}
"""

HOST_SCENARIOS = (
    ("m5_theater_surface_override_is_not_persisted", r"""
      const { I, persisted } = createHost();
      // 进入剧场的首次渲染：theaterPresentation 与 compact 同一次 setViewProps 到达。
      I.setViewProps({ theaterPresentation: { active: true }, chatSurfaceMode: 'compact' });
      assert.equal(I.state.chatSurfaceMode, 'compact');
      assert.deepEqual(persisted, [], '剧场强制的 compact 不能写入形态偏好');
      // 剧场期间其他入口的形态变化同样不写入。
      I.persistChatSurfaceModePreference('compact');
      assert.deepEqual(persisted, []);
      // 退出后用户的真实切换照常持久化。
      I.setViewProps({ theaterPresentation: { active: false } });
      I.setViewProps({ chatSurfaceMode: 'full' });
      assert.deepEqual(persisted, [['neko.reactChatWindow.chatSurfaceMode', 'full']]);
    """),
    ("l10_state_snapshot_exposes_external_input_lock", r"""
      const { I } = createHost();
      assert.equal(I.getStateSnapshot().composerExternallyLocked, false);
      I.state.homeTutorialInteractionLocked = true;
      assert.equal(I.getStateSnapshot().composerExternallyLocked, true);
      I.state.homeTutorialInteractionLocked = false; I.state.homeTutorialInputLocked = true;
      assert.equal(I.getStateSnapshot().composerExternallyLocked, true);
    """),
)


def _run(script: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script += "\nrun().then(() => process.stdout.write('ok')).catch(error => { console.error(error); process.exitCode = 1; });"
    result = run_node_stdin(node, script, cwd=str(ROOT), capture_output=True, check=False, timeout=20)
    assert result.returncode == 0, f"Node regression failed:\n{result.stdout}\n{result.stderr}"
    assert result.stdout == "ok", "async scenario did not complete"


@pytest.mark.parametrize("_name,scenario", RUNTIME_SCENARIOS, ids=[case[0] for case in RUNTIME_SCENARIOS])
def test_theater_runtime_round2_contracts(_name, scenario):
    _run(RUNTIME_HARNESS + "\nasync function run() {\n" + scenario + "\n}\n")


@pytest.mark.parametrize("_name,scenario", HOST_SCENARIOS, ids=[case[0] for case in HOST_SCENARIOS])
def test_chat_host_round2_contracts(_name, scenario):
    _run(HOST_HARNESS + "\nasync function run() {\n" + scenario + "\n}\n")


def _function_body(source: str, marker: str, length: int = 1400) -> str:
    start = source.index(marker)
    return source[start:start + length]


def test_ordinary_text_drop_and_avatar_entries_share_the_theater_guard():
    """Every ordinary entry point consults the local-or-peer theater check before sending."""
    source = (ROOT / "static/app/app-buttons.js").read_text(encoding="utf-8")
    helper = _function_body(source, "function isOrdinaryChatBlockedByTheater()", 400)
    assert "theaterRuntime.blocksOrdinaryChat()" in helper
    assert "theater.chatUnavailable" in source

    text = source[source.index("async function sendTextPayload(rawText, options)"):source.index("mod.sendTextPayload = sendTextPayload;")]
    assert text.index("isOrdinaryChatBlockedByTheater()") < text.index("return sendTextPayloadInternal(")

    drop_start = source.index("mod.sendAvatarDropPayload = async function sendAvatarDropPayload(payload)")
    drop = source[drop_start:source.index("return sendTextPayload(prompt", drop_start)]
    assert drop.index("isOrdinaryChatBlockedByTheater()") < drop.index("prepareAvatarDropTextMode()")

    avatar_start = source.index("async function sendAvatarInteractionPayload(payload)")
    avatar = source[avatar_start:source.index("mod.avatarInteractionContract", avatar_start)]
    assert avatar.index("isOrdinaryChatBlockedByTheater()") < avatar.index("S.socket.send(")
