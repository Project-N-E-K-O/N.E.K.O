'use strict';

// 锤子按钮（id 'agent'）点一下直接打开喵能力界面；新手教程运行时照旧弹小面板。
// 在 vm 里跑 methods-buttons.js 的真实源码：用 createButtonElement 造出按钮，
// 再挂一个和各渲染器一样的普通 click 监听（代表「弹小面板」），派发点击看谁被调到。

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const METHODS_SOURCE = fs.readFileSync(
    path.join(PROJECT_ROOT, 'static/avatar/avatar-ui-buttons/methods-buttons.js'),
    'utf8'
);

class FakeElement {
    constructor(tagName) {
        this.tagName = tagName;
        this.style = {};
        this.dataset = {};
        this.attributes = {};
        this.children = [];
        this.listeners = [];
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    appendChild(child) { this.children.push(child); return child; }
    addEventListener(type, listener, options) {
        const capture = options === true || !!(options && options.capture);
        this.listeners.push({ type, listener, capture });
    }
    // 点击落在按钮本身：先跑捕获阶段的监听，再跑普通监听；stopImmediatePropagation 之后都不再跑。
    click() {
        const event = {
            type: 'click',
            target: this,
            defaultPrevented: false,
            immediateStopped: false,
            preventDefault() { this.defaultPrevented = true; },
            stopPropagation() {},
            stopImmediatePropagation() { this.immediateStopped = true; }
        };
        const ordered = [
            ...this.listeners.filter((entry) => entry.type === 'click' && entry.capture),
            ...this.listeners.filter((entry) => entry.type === 'click' && !entry.capture)
        ];
        for (const entry of ordered) {
            if (event.immediateStopped) break;
            entry.listener(event);
        }
        return event;
    }
}

function load({ popupOpen = false, withHud = true, tutorial = {}, needsStream = false, windowBlocked = false } = {}) {
    const opened = [];
    const closedPopups = [];
    const popup = new FakeElement('div');
    popup.style.display = popupOpen ? 'flex' : 'none';
    popup.style.opacity = popupOpen ? '1' : '0';
    const document = {
        createElement: (tagName) => new FakeElement(tagName),
        getElementById: (id) => (id === 'live2d-popup-agent' ? popup : null),
        querySelectorAll: () => []
    };
    const window = {
        localStorage: { getItem() { return null; }, setItem() {} },
        addEventListener() {},
        dispatchEvent() { return true; }
    };
    if (withHud) {
        window.AgentHUD = { openPowerWindow() { opened.push('power'); return windowBlocked ? null : {}; } };
    }
    if (needsStream) window.computerUseNeedsCaptureStream = () => true;
    if (tutorial.isInTutorial) window.isInTutorial = true;
    if (tutorial.managerRunning) window.universalTutorialManager = { isTutorialRunning: true };
    if (tutorial.shortcutBlocked) window.isNekoShortcutBlockedByTutorial = () => true;

    const AvatarButtonMixin = { methods: {} };
    const context = vm.createContext({
        AvatarButtonMixin, clearTimeout, console, document, setTimeout, window
    });
    vm.runInContext(METHODS_SOURCE, context, { filename: 'methods-buttons.js' });

    const Proto = {};
    AvatarButtonMixin.methods.buttons(Proto, 'live2d', {});
    const manager = Object.create(Proto);
    manager._avatarPrefix = 'live2d';
    manager._avatarButtonOptions = { buttonClassPrefix: 'live2d-floating-btn' };
    manager.closePopupById = (id) => { closedPopups.push(id); return true; };

    function makeButton(id) {
        const config = Proto.getDefaultButtonConfigs().find((item) => item.id === id);
        const { btn } = manager.createButtonElement(config, new FakeElement('div'));
        const popupShown = [];
        // 各渲染器自己挂的普通监听：popupToggle 的按钮点一下就切换小面板。
        btn.addEventListener('click', () => { popupShown.push(id); });
        return { btn, popupShown };
    }

    return { window, opened, closedPopups, popup, makeButton, configs: Proto.getDefaultButtonConfigs() };
}

test('a plain click on the hammer opens NekoPower instead of the popup', () => {
    const { opened, closedPopups, makeButton } = load();
    const { btn, popupShown } = makeButton('agent');

    const event = btn.click();

    assert.deepEqual(opened, ['power']);
    assert.deepEqual(popupShown, []);
    assert.equal(event.defaultPrevented, true);
    assert.equal(event.immediateStopped, true);
    // As the hammer did before: the settings panel, its exclusive partner, is put away.
    assert.deepEqual(closedPopups, ['settings']);
});

test('where keyboard control needs a shared screen stream the hammer still opens the popup', () => {
    // Its keyboard switch asks for the stream within the click; the NekoPower page cannot.
    const { opened, makeButton } = load({ needsStream: true });
    const { btn, popupShown } = makeButton('agent');

    const event = btn.click();

    assert.deepEqual(opened, []);
    assert.deepEqual(popupShown, ['agent']);
    assert.equal(event.defaultPrevented, false);
});

test('a window that could not be opened leaves the click to the popup', () => {
    const { opened, closedPopups, makeButton } = load({ windowBlocked: true });
    const { btn, popupShown } = makeButton('agent');

    const event = btn.click();

    assert.deepEqual(opened, ['power']);
    assert.deepEqual(popupShown, ['agent']);
    assert.equal(event.immediateStopped, false);
    assert.deepEqual(closedPopups, []);
});

for (const [name, tutorial] of [
    ['window.isInTutorial', { isInTutorial: true }],
    ['universalTutorialManager.isTutorialRunning', { managerRunning: true }],
    ['isNekoShortcutBlockedByTutorial()', { shortcutBlocked: true }]
]) {
    test(`while the tutorial runs (${name}) the hammer still opens the popup`, () => {
        const { opened, makeButton } = load({ tutorial });
        const { btn, popupShown } = makeButton('agent');

        const event = btn.click();

        assert.deepEqual(opened, []);
        assert.deepEqual(popupShown, ['agent']);
        assert.equal(event.defaultPrevented, false);
    });
}

test('a popup left open is closed by the click as before, nothing new opens', () => {
    const { opened, makeButton } = load({ popupOpen: true });
    const { btn, popupShown } = makeButton('agent');

    btn.click();

    assert.deepEqual(opened, []);
    assert.deepEqual(popupShown, ['agent']);
});

test('without the HUD script the hammer falls back to the popup', () => {
    const { opened, makeButton } = load({ withHud: false });
    const { btn, popupShown } = makeButton('agent');

    btn.click();

    assert.deepEqual(opened, []);
    assert.deepEqual(popupShown, ['agent']);
});

test('other buttons are not intercepted', () => {
    const { opened, makeButton } = load();
    const { btn, popupShown } = makeButton('settings');

    btn.click();

    assert.deepEqual(opened, []);
    assert.deepEqual(popupShown, ['settings']);
});

// ---- common-ui-hud.js 的 AgentHUD.openPowerWindow：锤子打开的那个窗口 ----

const HUD_SOURCE = fs.readFileSync(path.join(PROJECT_ROOT, 'static/common-ui-hud.js'), 'utf8');

function loadHud({ openOrFocus = true, existing = null } = {}) {
    const calls = [];
    const makeWindow = (label) => ({
        label, closed: false, focused: 0, focus() { this.focused += 1; }
    });
    const window = {
        innerWidth: 1600,
        innerHeight: 900,
        location: { href: 'http://127.0.0.1:48911/', origin: 'http://127.0.0.1:48911' },
        addEventListener() {},
        open(url, name, features) {
            const win = makeWindow('window.open');
            calls.push({ how: 'open', url, name, features, win });
            return win;
        }
    };
    if (openOrFocus) {
        window.openOrFocusWindow = (url, name, features, options) => {
            const win = makeWindow('openOrFocusWindow');
            calls.push({ how: 'openOrFocusWindow', url, name, features, options, win });
            return win;
        };
    }
    if (existing) window._openedWindows = { neko_plugin_dashboard: existing };
    const document = {
        baseURI: 'http://127.0.0.1:48911/',
        getElementById: () => null,
        createElement: () => ({ style: {} }),
        head: { appendChild() {} },
        body: null
    };
    const context = vm.createContext({
        window, document, URL, console, setTimeout, clearTimeout,
        screen: { width: 1920, height: 1080 },
        localStorage: { getItem() { return null; }, setItem() {} },
        navigator: { userAgent: 'node' }
    });
    vm.runInContext(HUD_SOURCE, context, { filename: 'common-ui-hud.js' });
    return { window, calls, makeWindow };
}

test('openPowerWindow opens the NekoPower page in the plugin dashboard window', () => {
    const { window, calls } = loadHud();

    const opened = window.AgentHUD.openPowerWindow();

    assert.equal(calls.length, 1);
    const [call] = calls;
    assert.equal(call.how, 'openOrFocusWindow');
    assert.equal(call.name, 'neko_plugin_dashboard');
    const url = new URL(call.url);
    assert.equal(url.pathname, '/api/agent/user_plugin/dashboard');
    assert.equal(url.searchParams.get('yui_opener_origin'), 'http://127.0.0.1:48911');
    assert.equal(url.searchParams.has('v'), false);
    // 已经开着时只叫到前面：不重新载入，也不改用户调过的窗口大小。
    assert.equal(call.options.navigateOnReuse, false);
    assert.equal(call.options.preserveGeometryOnReuse, true);
    assert.equal(window._openedWindows.neko_plugin_dashboard, opened);
    assert.equal(opened.focused, 1);
});

test('without openOrFocusWindow an open window is only focused, not reloaded', () => {
    const existing = { closed: false, focused: 0, focus() { this.focused += 1; } };
    const { window, calls } = loadHud({ openOrFocus: false, existing });

    const opened = window.AgentHUD.openPowerWindow();

    assert.equal(calls.length, 0);
    assert.equal(opened, existing);
    assert.equal(existing.focused, 1);
});

test('without openOrFocusWindow and no window yet, window.open is used', () => {
    const { window, calls } = loadHud({ openOrFocus: false });

    window.AgentHUD.openPowerWindow();

    assert.equal(calls.length, 1);
    assert.equal(calls[0].how, 'open');
    assert.equal(calls[0].name, 'neko_plugin_dashboard');
});

test('the hammer keeps its id and popup config, so the tutorial still finds its panel', () => {
    const { configs, makeButton } = load();
    const { btn } = makeButton('agent');
    const agent = configs.find((item) => item.id === 'agent');

    assert.equal(btn.id, 'live2d-btn-agent');
    assert.equal(agent.hasPopup, true);
    assert.equal(agent.popupToggle, true);
});
