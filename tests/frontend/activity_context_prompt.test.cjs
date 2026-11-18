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
for (const context of ['work', 'play']) {
  test(`tutorial cannot revive an opted-out queued ${context} prompt`, async () => {
    const h = boot({deferred: true});
    const visionChatEnabled = context === 'work';
    h.window.appState.proactiveVisionChatEnabled = visionChatEnabled;
    const pending = h.window.appContextPrompt.handle(context);
    assert.equal(h.prompts.length, 1);
    h.window.appState.activityContextPromptEnabled = false;
    h.window.appContextPrompt.syncSetting();
    h.events.get('neko:home-tutorial-features-suppressed')({detail: {active: true}});
    h.window.appState.activityContextPromptEnabled = true;
    h.prompts[0].show();
    await pending;
    h.events.get('neko:home-tutorial-features-suppressed')({detail: {active: false}});
    await tick();
    assert.equal(h.prompts.length, 1);
    assert.equal(h.saves, 0);
    assert.equal(h.window.appState.proactiveVisionChatEnabled, visionChatEnabled);
  });
}
test('tutorial still replays an enabled queued prompt after suppression ends', async () => {
  const h = boot({deferred: true});
  const pending = h.window.appContextPrompt.handle('work');
  h.events.get('neko:home-tutorial-features-suppressed')({detail: {active: true}});
  h.prompts[0].show();
  await pending;
  assert.equal(h.prompts.length, 1);
  h.events.get('neko:home-tutorial-features-suppressed')({detail: {active: false}});
  await tick();
  assert.equal(h.prompts.length, 2);
  h.prompts[1].show();
  h.prompts[1].resolve('accept');
  await tick();
  assert.equal(h.saves, 1);
  assert.equal(h.window.appState.proactiveVisionChatEnabled, false);
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
test('disable invalidates the rest of an already-running replay batch', async () => {
  const h = boot({ready: false, deferred: true});
  h.window.appState.proactiveChatEnabled = false;
  await h.window.appContextPrompt.handle('work');
  await h.window.appContextPrompt.handle('play');
  h.window.nekoTelemetryBranch = 'main';
  h.events.get('neko:telemetry-branch-resolved')();
  assert.equal(h.prompts.length, 1);
  h.window.appState.activityContextPromptEnabled = false;
  h.window.appContextPrompt.syncSetting();
  h.window.appState.activityContextPromptEnabled = true;
  h.prompts[0].show();
  await tick();
  assert.equal(h.prompts.length, 1);
});
test('cross-window prompt changes preserve the proactive timer', async () => {
  const h = settingsBoot();
  await tick(); await tick();
  let schedules = 0;
  h.window.scheduleProactiveChat = () => schedules++;
  h.listeners.get('storage')({key: 'project_neko_settings', newValue: JSON.stringify({activityContextPromptEnabled: false})});
  assert.equal(h.window.appState.activityContextPromptEnabled, false);
  assert.equal(schedules, 0);
  h.listeners.get('storage')({key: 'project_neko_settings', newValue: JSON.stringify({proactiveChatInterval: 123})});
  assert.equal(schedules, 1);
});
for (const deferred of [false, true]) {
  test(`registered avatar hooks refresh settings (deferred=${deferred})`, () => {
    const checkboxes = new Map(['live2d', 'vrm', 'mmd'].map(prefix => [`#${prefix}-activity-context-prompt`, {checked: true}]));
    const events = new Map();
    const window = {activityContextPromptEnabled: true, addEventListener(name, fn) {events.set(name, fn);}, updateChatModeStyle() {}};
    const document = {getElementById() {return {};}, querySelector(selector) {return checkboxes.get(selector) ?? null;}};
    const sandbox = {window, document, requestAnimationFrame(fn) {fn();}, Live2DManager: class {}};
    if (!deferred) Object.assign(sandbox, {VRMManager: class {}, MMDManager: class {}});
    vm.createContext(sandbox);
    for (const file of ['avatar-ui-popup.js', 'avatar-ui-popup-config.js']) {
      vm.runInContext(fs.readFileSync(path.join(root, 'static/avatar', file), 'utf8'), sandbox);
    }
    if (deferred) {
      Object.assign(sandbox, {VRMManager: class {}, MMDManager: class {}});
      events.get('vrm-modules-ready')();
      events.get('mmd-modules-ready')();
    }
    for (const [name, prefix] of [['Live2DManager', 'live2d'], ['VRMManager', 'vrm'], ['MMDManager', 'mmd']]) {
      const manager = new sandbox[name]();
      assert.equal(typeof manager._onPopupShow, 'function');
      for (const enabled of [true, false, true]) {
        window.activityContextPromptEnabled = enabled;
        manager._onPopupShow({querySelector() {return null;}}, 'settings');
        assert.equal(checkboxes.get(`#${prefix}-activity-context-prompt`).checked, enabled);
      }
    }
  });
}
test('hydrating only the prompt preference does not reschedule proactive chat', async () => {
  const store = new Map([['project_neko_settings', JSON.stringify({activityContextPromptEnabled: true, proactiveVisionChatEnabled: true})]]);
  const h = settingsBoot(store, {activityContextPromptEnabled: false});
  let schedules = 0;
  h.window.scheduleProactiveChat = () => schedules++;
  await tick(); await tick();
  assert.equal(h.window.appState.activityContextPromptEnabled, false);
  assert.equal(schedules, 0);
});

function animationSettingsBoot(prefix, initiallyEnabled) {
  function element() {
    return {
      children: [], style: {}, attributes: new Map(), listeners: new Map(),
      appendChild(child) {this.children.push(child); child.parentElement = this; return child;},
      setAttribute(name, value) {this.attributes.set(name, String(value));},
      getAttribute(name) {return this.attributes.get(name) ?? null;},
      addEventListener(name, fn) {
        const handlers = this.listeners.get(name) || [];
        handlers.push(fn);
        this.listeners.set(name, handlers);
      },
      dispatchEvent(event) {
        for (const handler of this.listeners.get(event.type) || []) handler(event);
      },
      matches() {return false;},
      querySelector(selector) {
        for (const child of this.children) {
          if (selector === `#${child.id}`) return child;
          const found = child.querySelector(selector);
          if (found) return found;
        }
        return null;
      },
    };
  }
  const calls = [];
  const localStorage = {setItem() {assert.fail('popup synchronization must not persist settings');}};
  const document = {
    body: element(), createElement: element,
    getElementById() {return {};},
    querySelector(selector) {return this.body.querySelector(selector);},
  };
  const window = {
    mouseTrackingEnabled: initiallyEnabled,
    addEventListener() {}, localStorage,
    saveNEKOSettings() {calls.push(['save', window.mouseTrackingEnabled, manager._getMouseTrackingState()]);},
  };
  const sandbox = {
    window, document, localStorage, console: {log() {}},
    requestAnimationFrame(fn) {fn();},
    Live2DManager: class {}, VRMManager: class {}, MMDManager: class {},
  };
  vm.createContext(sandbox);
  for (const file of ['avatar-ui-popup.js', 'avatar-ui-popup-config.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, 'static/avatar', file), 'utf8'), sandbox);
  }
  const managerName = {live2d: 'Live2DManager', vrm: 'VRMManager', mmd: 'MMDManager'}[prefix];
  const manager = new sandbox[managerName]();
  manager.cursorFollow = {enabled: initiallyEnabled, setEnabled(enabled) {this.enabled = enabled;}};
  manager._createSidePanelContainer = () => Object.assign(element(), {_expand() {}});
  const toggle = manager._onMouseTrackingToggle;
  manager._onMouseTrackingToggle = enabled => {
    calls.push(['toggle', enabled]);
    toggle.call(manager, enabled);
  };
  const popup = element();
  document.body.appendChild(popup);
  const panel = manager._createAnimationSettingsSidePanel();
  const tracking = panel.querySelector(`#${prefix}-mouse-tracking-toggle`);
  const trackingRow = tracking.parentElement;
  const modeRow = trackingRow.parentElement.children[1];
  return {
    manager, window, popup, panel, tracking, trackingRow, modeRow, calls,
    setExternalState(enabled) {
      manager.cursorFollow.enabled = enabled;
      // Deliberately disagree with the MMD model to catch global-state overwrites.
      window.mouseTrackingEnabled = prefix === 'mmd' ? !enabled : enabled;
    },
    assertTrackingState(enabled) {
      assert.equal(tracking.checked, enabled, 'checkbox');
      assert.equal(trackingRow.children[1].children[0].style.opacity, enabled ? '1' : '0', 'indicator');
      assert.equal(trackingRow.getAttribute('aria-checked'), String(enabled), 'accessible switch state');
      assert.equal(modeRow.style.opacity, enabled ? '1' : '0.4', 'mode opacity');
      assert.equal(modeRow.style.pointerEvents, enabled ? 'auto' : 'none', 'mode pointer input');
      assert.equal(modeRow.tabIndex, enabled ? 0 : -1, 'mode keyboard focus');
    },
  };
}

for (const prefix of ['live2d', 'vrm', 'mmd']) {
  for (const initiallyEnabled of [false, true]) {
    test(`${prefix} retained popup fully refreshes tracking (initially ${initiallyEnabled}) without saving`, () => {
      const h = animationSettingsBoot(prefix, initiallyEnabled);
      // Animation settings are a body-mounted side panel, not popup descendants.
      assert.equal(h.popup.querySelector(`#${prefix}-mouse-tracking-toggle`), null);
      h.assertTrackingState(initiallyEnabled);
      for (const enabled of [!initiallyEnabled, initiallyEnabled]) {
        h.setExternalState(enabled);
        h.manager._onPopupShow(h.popup, 'settings');
        h.assertTrackingState(enabled);
        assert.equal(h.panel.querySelector(`#${prefix}-mouse-tracking-toggle`), h.tracking);
        assert.equal(h.window.mouseTrackingEnabled, prefix === 'mmd' ? !enabled : enabled);
        assert.deepEqual(h.calls, [], 'reopening must not toggle the model or persist settings');
      }
    });
  }

  test(`${prefix} tracking click and keyboard toggles synchronize before saving the new state`, () => {
    const h = animationSettingsBoot(prefix, false);
    for (const [type, key, enabled] of [['click', undefined, true], ['keydown', 'Enter', false], ['keydown', ' ', true]]) {
      h.trackingRow.dispatchEvent({type, key, stopPropagation() {}, preventDefault() {}});
      h.assertTrackingState(enabled);
      assert.deepEqual(h.calls.splice(0), [['toggle', enabled], ['save', enabled, enabled]]);
    }
  });
}
