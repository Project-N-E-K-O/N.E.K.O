'use strict';

// 悬浮窗显不显示由 agent 进程记着（喵能力界面里改），跟着状态快照的 task_hud_visible 过来；
// 主界面把它同步到本地存储（老代码照旧读那里），并刷新悬浮窗。
// 在 vm 里跑 common-ui-hud.js 和 agent_ui_v2.js 的真实源码。

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const HUD_SOURCE = fs.readFileSync(path.join(PROJECT_ROOT, 'static/common-ui-hud.js'), 'utf8');
const UI_V2_SOURCE = fs.readFileSync(path.join(PROJECT_ROOT, 'static/js/agent_ui_v2.js'), 'utf8');
const KEY = 'neko-agent-taskhud-visible';

function load({ stored, checkbox = true, storage = new Map(), saveOk = true } = {}) {
    if (stored !== undefined) storage.set(KEY, stored);
    const localStorage = {
        getItem(key) { return storage.has(key) ? storage.get(key) : null; },
        setItem(key, value) { storage.set(key, String(value)); }
    };
    const sent = [];
    const toggled = [];
    const box = checkbox ? { id: 'live2d-agent-taskhud', checked: storage.get(KEY) !== 'false', styled: 0, _updateStyle() { this.styled += 1; } } : null;
    const document = {
        baseURI: 'http://127.0.0.1:48911/',
        body: null,
        head: { appendChild() {} },
        createElement: () => ({ style: {}, appendChild() {}, setAttribute() {} }),
        getElementById: (id) => (box && id === box.id ? box : null),
        querySelectorAll: () => []
    };
    const window = {
        innerWidth: 1600,
        innerHeight: 900,
        location: { href: 'http://127.0.0.1:48911/', origin: 'http://127.0.0.1:48911' },
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() { return true; },
        localStorage,
        checkAndToggleTaskHUD(event) { toggled.push(event); }
    };
    const fetch = (url, init) => {
        sent.push({ url, body: init && init.body ? JSON.parse(init.body) : null });
        return saveOk.length === undefined
            ? Promise.resolve({ ok: !!saveOk, json: () => Promise.resolve({ success: !!saveOk }) })
            : Promise.resolve({ ok: !!saveOk[sent.length - 1], json: () => Promise.resolve({}) });
    };
    window.fetch = fetch;
    const context = vm.createContext({
        window, document, localStorage, fetch, URL, console, setTimeout, clearTimeout,
        screen: { width: 1920, height: 1080 },
        navigator: { userAgent: 'node' },
        performance: { now: () => 0 }
    });
    vm.runInContext(HUD_SOURCE, context, { filename: 'common-ui-hud.js' });
    return { context, window, storage, sent, toggled, box };
}

test('a setting from the agent process is mirrored, the popup box follows and the HUD is redone', () => {
    const { window, storage, toggled, box } = load();

    assert.equal(window.AgentHUD.syncTaskHudPreference(false), true);
    assert.equal(storage.get(KEY), 'false');
    assert.equal(box.checked, false);
    assert.equal(box.styled, 1);
    assert.equal(toggled.length, 1);

    // The same again changes nothing and redoes nothing.
    assert.equal(window.AgentHUD.syncTaskHudPreference(false), false);
    assert.equal(toggled.length, 1);

    assert.equal(window.AgentHUD.syncTaskHudPreference(true), true);
    assert.equal(storage.get(KEY), 'true');
    assert.equal(box.checked, true);
    assert.equal(toggled.length, 2);
});

test('never set there: a HUD switched off here before is handed in once', () => {
    const { window, storage, sent, toggled } = load({ stored: 'false' });

    assert.equal(window.AgentHUD.syncTaskHudPreference(null), false);
    assert.equal(window.AgentHUD.syncTaskHudPreference(null), false);

    assert.deepEqual(sent.map((item) => item.url), ['/api/agent/command']);
    assert.deepEqual(sent[0].body, { command: 'set_task_hud_visible', value: false });
    assert.equal(storage.get(KEY), 'false');
    assert.equal(toggled.length, 0);
});

test('a hand-in that did not get through is tried again with the next snapshot, and only until it does', async () => {
    const { window, sent } = load({ stored: 'false', saveOk: [false, true] });
    const settle = () => new Promise((resolve) => setImmediate(resolve));

    window.AgentHUD.syncTaskHudPreference(null);
    await settle();
    window.AgentHUD.syncTaskHudPreference(null);
    await settle();
    window.AgentHUD.syncTaskHudPreference(null);
    await settle();

    assert.equal(sent.length, 2);
});

test('a backend that never takes it is not asked again with every snapshot', async () => {
    const { window, sent } = load({ stored: 'false', saveOk: false });
    const settle = () => new Promise((resolve) => setImmediate(resolve));

    for (let i = 0; i < 6; i += 1) {
        window.AgentHUD.syncTaskHudPreference(null);
        await settle();
    }

    assert.equal(sent.length, 3);
});

function popupCheckbox(harness) {
    // _createAgentPopupContent builds the popup; only the task HUD row is looked at here.
    const listeners = {};
    const checkbox = {
        checked: true, styled: 0,
        _updateStyle() { this.styled += 1; },
        addEventListener(type, fn) { listeners[type] = fn; }
    };
    const manager = {
        _avatarPrefix: 'live2d',
        _createToggleItem(toggle) {
            return {
                querySelector: (selector) => (selector === '#live2d-agent-taskhud' ? checkbox : null),
                appendChild() {}
            };
        }
    };
    const popup = { style: {}, appendChild() {} };
    harness.window.AgentHUD._createAgentPopupContent.call(manager, popup);
    return { checkbox, change: () => listeners.change({ target: checkbox }) };
}

test('the popup box that could not be saved is put back, rather than reverted later by a snapshot', async () => {
    const harness = load({ saveOk: false });
    const { checkbox, change } = popupCheckbox(harness);
    const settle = () => new Promise((resolve) => setImmediate(resolve));

    checkbox.checked = false;
    change();
    assert.equal(harness.storage.get(KEY), 'false');
    await settle();

    assert.deepEqual(harness.sent.map((item) => item.body), [{ command: 'set_task_hud_visible', value: false }]);
    assert.equal(checkbox.checked, true);
    assert.equal(harness.storage.get(KEY), 'true');
});

test('the popup box that was saved stays as it is', async () => {
    const harness = load();
    const { checkbox, change } = popupCheckbox(harness);
    const settle = () => new Promise((resolve) => setImmediate(resolve));

    checkbox.checked = false;
    change();
    await settle();

    assert.equal(checkbox.checked, false);
    assert.equal(harness.storage.get(KEY), 'false');
});

test('two pages share their storage but each puts its own box and HUD right', () => {
    const storage = new Map();
    const first = load({ storage });
    const second = load({ storage });

    assert.equal(first.window.AgentHUD.syncTaskHudPreference(false), true);
    // The storage says "off" already; the second page has not followed it yet.
    assert.equal(storage.get(KEY), 'false');
    assert.equal(second.box.checked, true);

    assert.equal(second.window.AgentHUD.syncTaskHudPreference(false), true);
    assert.equal(second.box.checked, false);
    assert.equal(second.toggled.length, 1);
    // And the same again is nothing on either page.
    assert.equal(second.window.AgentHUD.syncTaskHudPreference(false), false);
    assert.equal(first.window.AgentHUD.syncTaskHudPreference(false), false);
});

test('never set there and shown here: nothing is sent', () => {
    const { window, sent } = load();

    window.AgentHUD.syncTaskHudPreference(null);

    assert.deepEqual(sent, []);
});

test('an older agent process that does not send it changes nothing and is sent nothing', () => {
    const { window, storage, sent, toggled } = load({ stored: 'false' });

    assert.equal(window.AgentHUD.syncTaskHudPreference(undefined), false);

    assert.deepEqual(sent, []);
    assert.equal(storage.get(KEY), 'false');
    assert.equal(toggled.length, 0);
});

test('saveTaskHudPreference sends the command the agent process keeps it by', async () => {
    const { window, sent } = load();

    assert.equal(await window.AgentHUD.saveTaskHudPreference(true), true);

    assert.deepEqual(sent, [{ url: '/api/agent/command', body: { command: 'set_task_hud_visible', value: true } }]);
});

test('a status snapshot that reaches the page is synced through the HUD', () => {
    const harness = load();
    const { context, window, storage, toggled } = harness;
    vm.runInContext(UI_V2_SOURCE, context, { filename: 'agent_ui_v2.js' });

    window.applyAgentStatusSnapshotToUI({ revision: 7, flags: {}, capabilities: {}, task_hud_visible: false });

    assert.equal(storage.get(KEY), 'false');
    assert.equal(toggled.length, 1);
});
