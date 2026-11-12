'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
function boot({enabled, ready = true, deferred = false} = {}) {
  const events = new Map(), prompts = [];
  let saves = 0;
  const window = {
    appState: {proactiveChatEnabled: true, proactiveVisionChatEnabled: true},
    addEventListener(name, fn) { events.set(name, fn); },
    saveNEKOSettings() { saves++; },
    showDecisionPrompt(config) {
      return new Promise(resolve => {
        const prompt = {config, resolve, show() {
          config.onShown({overlay: {click() {resolve('decline');}}});
        }};
        prompts.push(prompt);
        if (!deferred) prompt.show();
      });
    },
  };
  if (enabled !== undefined) window.appState.activityContextPromptEnabled = enabled;
  if (ready) window.nekoTelemetryBranch = 'main';
  vm.runInNewContext(fs.readFileSync(path.join(root, 'static/app/app-context-prompt.js'), 'utf8'), {window, console, queueMicrotask});
  return {window, prompts, events, get saves() {return saves;}};
}
for (const context of ['work', 'play']) {
  test(`disabled drops ${context}, re-enable accepts a fresh event`, async () => {
    const h = boot({enabled: false});
    if (context === 'play') h.window.appState.proactiveVisionChatEnabled = false;
    await h.window.appContextPrompt.handle(context);
    assert.equal(h.prompts.length, 0);
    h.window.appState.activityContextPromptEnabled = true;
    const pending = h.window.appContextPrompt.handle(context);
    assert.equal(h.prompts.length, 1);
    h.prompts[0].resolve('decline'); await pending;
    assert.equal(h.saves, 0);
  });
}
test('missing setting preserves per-session prompts and decline never changes sources', async () => {
  const h = boot();
  const pending = h.window.appContextPrompt.handle('work');
  h.prompts[0].resolve('decline'); await pending;
  await h.window.appContextPrompt.handle('work');
  assert.equal(h.prompts.length, 1);
  assert.equal(h.window.appState.proactiveVisionChatEnabled, true);
  assert.equal(h.saves, 0);
});
test('disable clears pre-hydration and tutorial signals without replay on enabling', async () => {
  const h = boot({ready: false});
  await h.window.appContextPrompt.handle('work');
  h.window.appState.activityContextPromptEnabled = false;
  h.window.appContextPrompt.syncSetting();
  h.window.appState.activityContextPromptEnabled = true;
  h.window.nekoTelemetryBranch = 'main';
  h.events.get('neko:telemetry-branch-resolved')();
  await Promise.resolve();
  assert.equal(h.prompts.length, 0);
  h.events.get('neko:home-tutorial-features-suppressed')({detail: {active: true}});
  await h.window.appContextPrompt.handle('work');
  h.window.appState.activityContextPromptEnabled = false;
  h.window.appContextPrompt.syncSetting();
  h.window.appState.activityContextPromptEnabled = true;
  h.events.get('neko:home-tutorial-features-suppressed')({detail: {active: false}});
  await Promise.resolve();
  assert.equal(h.prompts.length, 0);
});
for (const deferred of [false, true]) {
  test(`disable dismisses ${deferred ? 'queued' : 'visible'} prompt`, async () => {
    const h = boot({deferred});
    const pending = h.window.appContextPrompt.handle('work');
    h.window.appState.activityContextPromptEnabled = false;
    h.window.appContextPrompt.syncSetting();
    if (deferred) h.prompts[0].show();
    await pending;
    assert.equal(h.saves, 0);
    assert.equal(h.window.appState.proactiveVisionChatEnabled, true);
  });
}
test('an acceptance racing with disable cannot alter screen sharing', async () => {
  const h = boot({deferred: true});
  const pending = h.window.appContextPrompt.handle('work');
  h.prompts[0].resolve('accept');
  h.window.appState.activityContextPromptEnabled = false;
  h.window.appContextPrompt.syncSetting();
  h.window.appState.activityContextPromptEnabled = true;
  await pending;
  assert.equal(h.saves, 0);
  assert.equal(h.window.appState.proactiveVisionChatEnabled, true);
});
test('enabled acceptance still saves the screen-source choice', async () => {
  const h = boot();
  const pending = h.window.appContextPrompt.handle('work');
  h.prompts[0].resolve('accept'); await pending;
  assert.equal(h.window.appState.proactiveVisionChatEnabled, false);
  assert.equal(h.saves, 1);
});

function settingsBoot(store = new Map(), server = {}) {
  const listeners = new Map(), posts = [];
  const sandbox = {
    console: {log() {}, warn() {}, error() {}},
    CustomEvent: class {constructor(type, init) {this.type = type; this.detail = init?.detail;}},
    setInterval() {return 0;}, clearInterval() {},
    setTimeout(fn, ms) {return setTimeout(fn, ms).unref();}, clearTimeout,
    localStorage: {getItem(k) {return store.get(k) ?? null;}, setItem(k,v) {store.set(k,v);}, removeItem(k) {store.delete(k);}},
    document: {getElementById() {return null;}},
    fetch: async (url, opts) => {
      if (opts?.method === 'POST') posts.push(JSON.parse(opts.body));
      return {ok: true, json: async () => ({success: true, settings: server, telemetryBranch: 'main'})};
    },
  };
  sandbox.window = {
    appState: {activityContextPromptEnabled: true, proactiveVisionChatEnabled: true},
    appConst: {}, appUtils: {mapRenderQualityToFollowPerf() {return 'medium';}},
    addEventListener(type, fn) {listeners.set(type, fn);},
    removeEventListener() {}, dispatchEvent() {},
    appContextPrompt: {syncSetting() {sandbox.synced = sandbox.window.appState.activityContextPromptEnabled;}},
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'static/app/app-settings.js'), 'utf8'), sandbox);
  return {sandbox, window: sandbox.window, listeners, store, posts};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
test('settings save, server hydration, reload, and cross-window changes carry the opt-out', async () => {
  const key = 'activityContextPromptEnabled';
  const h = settingsBoot(new Map(), {[key]: false});
  await tick(); await tick();
  assert.equal(h.window.appState[key], false);
  assert.equal(h.window.appSettings.getConversationSettings()[key], false);
  assert.equal(h.sandbox.synced, false);
  h.window[key] = true;
  h.window.appSettings.saveSettings();
  await tick(); await tick();
  assert.equal(h.window.appState[key], true);
  assert.equal(JSON.parse(h.store.get('project_neko_settings'))[key], true);
  assert.equal(h.posts.at(-1)[key], true);
  h.window[key] = false;
  h.window.appSettings.saveSettings();
  await tick(); await tick();
  const reboot = settingsBoot(h.store, {[key]: false});
  await tick(); await tick();
  assert.equal(reboot.window.appState[key], false);
  reboot.listeners.get('storage')({key: 'project_neko_settings', newValue: JSON.stringify({[key]: true})});
  assert.equal(reboot.window.appState[key], true);
  assert.equal(reboot.sandbox.synced, true);
});
