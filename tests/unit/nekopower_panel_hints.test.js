'use strict';

// 喵能力面板的两句提示：
// - 只开了总开关、键鼠/浏览器/插件一项都没开时，喵能力其实没开着（agent 进程的 nekopower_on），
//   状态栏说要再开一项（agent_ui_v2.js 的 render）。
// - 桌面版的省算力模式正把桌宠窗口裁到模型附近时，任务悬浮窗显示不出来，「显示悬浮窗」下面说一句
//   （common-ui-hud.js 建出这行、平时藏着；agent_ui_v2.js 的 render 在裁剪期间打开它）。
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

function load({ crop = null, peekHostStage = false } = {}) {
    const elements = new Map();
    for (const id of ['live2d-agent-master', 'live2d-agent-keyboard', 'live2d-agent-browser',
        'live2d-agent-user-plugin', 'live2d-agent-status']) {
        elements.set(id, new FakeElement('input', id));
    }
    const note = new FakeElement('div', NOTE_ID);
    note.hidden = true;
    elements.set(NOTE_ID, note);
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
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() { return true; },
        localStorage: { getItem: () => null, setItem() {} },
        // The key comes back as the text: what is shown is told by which key it is.
        t: (key) => key
    };
    if (crop) window.__nekoNiriPetPhysicalCrop = crop;
    if (peekHostStage) window.__nekoLive2DPeekHostStage = { version: 1, prepare() { return false; } };
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
    return { window, document, elements, note, show, status: () => elements.get('live2d-agent-status').textContent };
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
