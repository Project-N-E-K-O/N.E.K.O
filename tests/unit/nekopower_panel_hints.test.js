'use strict';

// 喵能力面板的两句提示：
// - 只开了总开关、键鼠/浏览器/插件一项都没开时，喵能力其实没开着（agent 进程的 nekopower_on），
//   状态栏说要再开一项（agent_ui_v2.js 的 render）。
// - 桌面版的省算力模式正把桌宠窗口裁到模型附近时，任务悬浮窗不显示（common-ui-hud.js 的
//   showAgentTaskHUD 不让它出来，裁剪开始时藏起、结束时照常判断），「显示悬浮窗」下面说一句
//   （common-ui-hud.js 建出这行、平时藏着；agent_ui_v2.js 在裁剪期间打开它）。
// 在 vm 里跑 common-ui-hud.js 和 agent_ui_v2.js 的真实源码。

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const HUD_SOURCE = fs.readFileSync(path.join(PROJECT_ROOT, 'static/common-ui-hud.js'), 'utf8');
const UI_V2_SOURCE = fs.readFileSync(path.join(PROJECT_ROOT, 'static/js/agent_ui_v2.js'), 'utf8');
const NOTE_ID = 'live2d-agent-taskhud-crop-note';

class FakeElement {
    constructor(tag = 'div', id = '') {
        this.tagName = tag.toUpperCase();
        this.id = id;
        this.style = {};
        this.children = [];
        this.attributes = new Map();
        this.hidden = false;
        this.textContent = '';
        this.checked = false;
        this.disabled = false;
        this.title = '';
    }
    appendChild(child) { this.children.push(child); return child; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
    addEventListener() {}
    removeEventListener() {}
    querySelector() { return null; }
    querySelectorAll() { return []; }
}

function load({ crop = null, peekHostStage = false, pluginContent = false } = {}) {
    const elements = new Map();
    for (const id of ['live2d-agent-master', 'live2d-agent-keyboard', 'live2d-agent-browser',
        'live2d-agent-user-plugin', 'live2d-agent-status']) {
        elements.set(id, new FakeElement('input', id));
    }
    const note = new FakeElement('div', NOTE_ID);
    note.hidden = true;
    elements.set(NOTE_ID, note);
    // The task HUD, already made and hidden, as the page has it once it was shown before.
    const hud = new FakeElement('div', 'agent-task-hud');
    hud.style.display = 'none';
    hud.style.opacity = '0';
    hud.getBoundingClientRect = () => ({ left: 0, top: 0, width: 300, height: 200 });
    elements.set('agent-task-hud', hud);
    const listeners = new Map();
    const toggled = [];
    const document = {
        baseURI: 'http://127.0.0.1:48911/',
        body: null,
        head: { appendChild() {} },
        createElement: (tag) => new FakeElement(tag),
        getElementById: (id) => elements.get(id) || null,
        querySelectorAll: () => []
    };
    const window = {
        innerWidth: 1600,
        innerHeight: 900,
        location: { href: 'http://127.0.0.1:48911/', origin: 'http://127.0.0.1:48911' },
        addEventListener(type, listener) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(listener);
        },
        removeEventListener() {},
        dispatchEvent() { return true; },
        localStorage: { getItem: () => null, setItem() {} },
        // The key comes back as the text: what is shown is told by which key it is.
        t: (key) => key,
        checkAndToggleTaskHUD() { toggled.push('check'); }
    };
    if (crop) window.__nekoNiriPetPhysicalCrop = crop;
    if (peekHostStage) window.__nekoLive2DPeekHostStage = { version: 1, prepare() { return false; } };
    // A plugin view sits in the HUD: it is shown even with the floating-window switch off.
    if (pluginContent) window.NekoPluginViews = { hasContent: () => true, syncHud() {}, clear() {} };
    const fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true }) });
    window.fetch = fetch;
    const context = vm.createContext({
        window, document, localStorage: window.localStorage, fetch, URL, console, setTimeout, clearTimeout,
        screen: { width: 1920, height: 1080 },
        navigator: { userAgent: 'node' },
        performance: { now: () => 0 }
    });
    vm.runInContext(HUD_SOURCE, context, { filename: 'common-ui-hud.js' });
    vm.runInContext(UI_V2_SOURCE, context, { filename: 'agent_ui_v2.js' });
    let revision = 0;
    const show = (snapshot) => window.applyAgentStatusSnapshotToUI({
        revision: (revision += 1),
        server_online: true,
        capabilities: {},
        task_hud_visible: true,
        ...snapshot
    });
    // The shell applied a crop state: every listener on the page hears it.
    const cropApplied = () => (listeners.get('neko:niri-pet-physical-crop-state-applied') || [])
        .forEach((listener) => listener({ type: 'neko:niri-pet-physical-crop-state-applied', detail: {} }));
    return {
        window, document, elements, note, hud, toggled, show, cropApplied,
        status: () => elements.get('live2d-agent-status').textContent
    };
}

const flags = (on = []) => ({
    agent_enabled: true,
    computer_use_enabled: on.includes('computer_use_enabled'),
    browser_use_enabled: on.includes('browser_use_enabled'),
    user_plugin_enabled: on.includes('user_plugin_enabled')
});

test('the master switch alone asks for one more switch, for nothing is done without one', () => {
    const h = load();
    h.show({ analyzer_enabled: true, flags: flags() });
    assert.equal(h.status(), 'agent.status.noCapability');
});

for (const capability of ['computer_use_enabled', 'browser_use_enabled', 'user_plugin_enabled']) {
    test(`with ${capability} on as well, NekoPower is on`, () => {
        const h = load();
        h.show({ analyzer_enabled: true, flags: flags([capability]) });
        assert.equal(h.status(), 'agent.status.enabled');
    });
}

test('with the master switch off, the status is as before', () => {
    const h = load();
    h.show({ analyzer_enabled: false, flags: { ...flags(), agent_enabled: false } });
    assert.equal(h.status(), 'agent.status.ready');
});

test('the note under "show floating window" is shown while the power-saving crop is on', () => {
    const h = load({ crop: { isActive: () => true }, peekHostStage: true });
    h.show({ analyzer_enabled: true, flags: flags(['user_plugin_enabled']) });
    assert.equal(h.note.hidden, false);
});

test('the note stays hidden with no crop, a crop not in force, or the Linux crop', () => {
    const cases = [
        { name: 'no crop', options: { peekHostStage: true } },
        { name: 'crop not in force', options: { crop: { isActive: () => false }, peekHostStage: true } },
        // Read as avatar-popup-common.js reads it: with no isActive it is not taken as cropping.
        { name: 'crop object without isActive', options: { crop: {}, peekHostStage: true } },
        // Niri / Hyprland crop the window too, but that is not the power-saving mode.
        { name: 'Linux physical crop', options: { crop: { isActive: () => true } } }
    ];
    for (const { name, options } of cases) {
        const h = load(options);
        h.show({ analyzer_enabled: true, flags: flags(['user_plugin_enabled']) });
        assert.equal(h.note.hidden, true, name);
    }
});

test('the note goes away again once the crop ends', () => {
    let active = true;
    const h = load({ crop: { isActive: () => active }, peekHostStage: true });
    h.show({ analyzer_enabled: true, flags: flags(['user_plugin_enabled']) });
    assert.equal(h.note.hidden, false);
    active = false;
    h.show({ analyzer_enabled: true, flags: flags(['user_plugin_enabled']) });
    assert.equal(h.note.hidden, true);
});

test('the popup puts the note right under the floating-window switch, hidden, in the panel language', () => {
    const h = load();
    const popup = new FakeElement('div', 'live2d-popup-agent');
    const items = [];
    const manager = {
        _avatarPrefix: 'live2d',
        _createToggleItem(toggle) {
            const item = new FakeElement('div', `${toggle.id}-item`);
            const box = new FakeElement('input', `live2d-${toggle.id}`);
            item.querySelector = () => box;
            items.push(item);
            return item;
        }
    };
    h.window.AgentHUD._createAgentPopupContent.call(manager, popup);
    const ids = popup.children.map((child) => child.id || child.className);
    const at = ids.indexOf(NOTE_ID);
    assert.notEqual(at, -1, 'the note is in the popup');
    assert.equal(ids[at - 1], 'agent-taskhud-item', 'right under the floating-window switch');
    assert.equal(ids[at + 1], 'live2d-settings-separator', 'before the separator');
    const note = popup.children[at];
    assert.equal(note.hidden, true);
    assert.equal(note.getAttribute('data-i18n'), 'settings.toggles.taskHudHiddenByPowerSave');
    assert.equal(note.textContent, 'settings.toggles.taskHudHiddenByPowerSave');
});

// ---- 省算力裁剪期间不显示任务悬浮窗（owner 2026-11-26 定）----

test('the task HUD is shown as before when nothing is cropped', () => {
    const h = load({ peekHostStage: true });
    h.window.AgentHUD.showAgentTaskHUD();
    assert.equal(h.hud.style.display, 'flex');
    assert.equal(h.hud.style.opacity, '1');
});

test('the task HUD is not shown while the power-saving crop is on, whatever asks for it', () => {
    const h = load({ crop: { isActive: () => true }, peekHostStage: true });
    h.hud.style.display = 'flex';
    h.hud.style.opacity = '1';
    h.window.AgentHUD.showAgentTaskHUD();
    assert.equal(h.hud.style.opacity, '0', 'faded out instead of shown');
    h.window.AgentHUD.showAgentTaskHUD({ ignoreVisibilityPreference: true });
    assert.equal(h.hud.style.opacity, '0', 'nor does asking past the floating-window switch');
});

test('plugin content: shown past the switch as before, not while cropping, back as the crop ends', () => {
    let active = false;
    const h = load({ crop: { isActive: () => active }, peekHostStage: true, pluginContent: true });
    h.window.localStorage.getItem = (key) => (key === 'neko-agent-taskhud-visible' ? 'false' : null);
    h.window.AgentHUD.showAgentTaskHUD();
    assert.equal(h.hud.style.opacity, '1', 'with the switch off, plugin content still shows it');
    active = true;
    h.cropApplied();
    assert.equal(h.hud.style.opacity, '0', 'gone as the crop starts');
    h.window.AgentHUD.showAgentTaskHUD();
    assert.equal(h.hud.style.opacity, '0', 'plugin content does not bring it back while cropping');
    active = false;
    h.cropApplied();
    assert.equal(h.hud.style.opacity, '1', 'back as the crop ends');
});

test('the Linux physical crop does not hide the task HUD', () => {
    const h = load({ crop: { isActive: () => true } });
    h.window.AgentHUD.showAgentTaskHUD();
    assert.equal(h.hud.style.opacity, '1');
});

test('the HUD goes as the crop starts, and is weighed again as it ends', () => {
    let active = false;
    const h = load({ crop: { isActive: () => active }, peekHostStage: true });
    h.window.AgentHUD.showAgentTaskHUD();
    assert.equal(h.hud.style.opacity, '1');

    active = true;
    h.cropApplied();
    assert.equal(h.hud.style.opacity, '0');
    assert.equal(h.note.hidden, false, 'the note under the switch follows the crop too');
    // The shell applies a crop state over and over while cropping: nothing more is done.
    h.cropApplied();
    assert.deepEqual(h.toggled, []);

    active = false;
    h.cropApplied();
    assert.deepEqual(h.toggled, ['check'], 'the usual rules decide whether it comes back');
    assert.equal(h.note.hidden, true);
    h.cropApplied();
    assert.deepEqual(h.toggled, ['check'], 'and only once, not with every state applied');
});

// checkAndToggleTaskHUD 在开关已关、还有任务时什么都不做（这里的替身也什么都不做）：裁剪结束时自己叫回来（评审 #4147）。
test('as the crop ends, a task still under way brings the HUD back even with the switches off', () => {
    for (const status of ['running', 'queued', 'waiting_user']) {
        let active = true;
        const h = load({ crop: { isActive: () => active }, peekHostStage: true });
        h.window._agentTaskMap = new Map([['t1', { id: 't1', status }]]);
        h.window.AgentHUD.showAgentTaskHUD();
        assert.equal(h.hud.style.opacity, '0', status);
        active = false;
        h.cropApplied();
        assert.equal(h.hud.style.opacity, '1', status);
    }
});

test('as the crop ends, nothing under way leaves the HUD to the usual rules', () => {
    for (const tasks of [[], [['t1', { id: 't1', status: 'completed' }]]]) {
        let active = true;
        const h = load({ crop: { isActive: () => active }, peekHostStage: true });
        h.window._agentTaskMap = new Map(tasks);
        h.window.AgentHUD.showAgentTaskHUD();
        active = false;
        h.cropApplied();
        assert.equal(h.hud.style.opacity, '0', JSON.stringify(tasks));
    }
});

test('as the crop ends, the floating-window switch turned off keeps it away', () => {
    let active = true;
    const h = load({ crop: { isActive: () => active }, peekHostStage: true });
    h.window.localStorage.getItem = (key) => (key === 'neko-agent-taskhud-visible' ? 'false' : null);
    h.window._agentTaskMap = new Map([['t1', { id: 't1', status: 'running' }]]);
    active = false;
    h.cropApplied();
    assert.equal(h.hud.style.opacity, '0');
});
