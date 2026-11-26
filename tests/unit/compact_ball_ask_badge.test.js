'use strict';

// 聊天窗缩成小球时，喵能力有问话在等回话就给小球挂角标（static/app/app-react-chat-window/
// minimize-and-idle-dock.js）。React 根组件经 onNekoPowerAskWaitingChange 上报，宿主据此：
// 给外壳标 data-nekopower-ask-waiting、在小球（拖拽手柄）里放角标、小球态下悬停说明一句，
// 并派发 react-chat-window:nekopower-ask-waiting-change 给桌面壳另开窗口画的小球用。
// 在 vm 里跑真实源码，DOM 用最小的假元素。

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const DOCK_SOURCE = fs.readFileSync(
    path.join(ROOT, 'static/app/app-react-chat-window/minimize-and-idle-dock.js'),
    'utf8'
);
const GEOMETRY_SOURCE = fs.readFileSync(
    path.join(ROOT, 'static/app/app-react-chat-window/geometry-and-messages.js'),
    'utf8'
);
const CSS_SOURCE = fs.readFileSync(path.join(ROOT, 'static/css/index.css'), 'utf8');

class FakeElement {
    constructor(tagName) {
        this.tagName = String(tagName).toUpperCase();
        this.className = '';
        this.children = [];
        this.attributes = new Map();
        const classes = new Set();
        this.classList = {
            toggle(name, force) { if (force) classes.add(name); else classes.delete(name); },
            contains: (name) => classes.has(name),
        };
    }
    appendChild(child) {
        this.children.push(child);
        child.parentNode = this;
        return child;
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
    removeAttribute(name) { this.attributes.delete(name); }
    hasAttribute(name) { return this.attributes.has(name); }
    all() {
        return this.children.flatMap((child) => [child].concat(child.all ? child.all() : []));
    }
    querySelector(selector) {
        assert.ok(selector.startsWith('.'), `only class selectors are faked, got ${selector}`);
        const name = selector.slice(1);
        return this.all().find((child) => String(child.className).split(/\s+/).includes(name)) || null;
    }
}

function createHarness({ minimized = true, surfaceMode = 'minimized', withHeader = true } = {}) {
    const shell = new FakeElement('div');
    const header = new FakeElement('div');
    if (withHeader) shell.appendChild(header);
    const emitted = [];
    const parts = {
        minimized,
        EVENT_PREFIX: 'react-chat-window:',
        lastRestorableChatSurfaceMode: 'compact',
        idleCatPlaygroundYarnSkinActive: false,
        getShell: () => shell,
        getHeader: () => (withHeader ? header : null),
        getMinimizeButton: () => null,
        getMinimizeIcon: () => null,
        getCurrentChatSurfaceMode: () => surfaceMode,
        getCurrentCompactChatState: () => 'input',
        coerceChatSurfaceModeForHost: (mode) => mode,
        scheduleCompactMinimizeBallTracking() {},
        getI18nText: (key, fallback) => (key === 'chat.nekoPowerAskWaitingBadge' ? 'A question waits' : fallback),
    };
    parts.dispatchHostEvent = (name, detail) => emitted.push({ type: parts.EVENT_PREFIX + name, detail });
    const window = {
        __appReactChatWindowParts: parts,
        reactChatWindowHost: {},
        addEventListener() {},
        dispatchEvent() { return true; },
        setTimeout() { return 1; },
        clearTimeout() {},
    };
    const document = {
        createElement: (tagName) => new FakeElement(tagName),
        addEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    vm.runInNewContext(DOCK_SOURCE, {
        window, document, console, Promise, Date, Number, Math, Object, Array, String, Boolean, JSON,
        CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    }, { filename: 'minimize-and-idle-dock.js' });
    return {
        parts,
        shell,
        header,
        emitted,
        badges: () => shell.all().filter((child) => child.className === 'react-chat-minimized-ask-badge'),
        setSurface(nextMinimized, nextMode) { parts.minimized = nextMinimized; surfaceMode = nextMode; },
    };
}

test('a question waiting marks the ball, and the mark goes once it is answered or over', () => {
    const h = createHarness();
    assert.equal(h.parts.nekoPowerAskWaiting, false);
    // A host callback of the React chat: zod checks that it returns nothing.
    assert.equal(h.parts.handleNekoPowerAskWaitingChange(true), undefined);
    assert.equal(h.shell.getAttribute('data-nekopower-ask-waiting'), 'true');
    const badges = h.badges();
    assert.equal(badges.length, 1);
    // In the ball (the drag handle, where the clicks and drags land), not apart from it.
    assert.equal(badges[0].parentNode, h.header);
    assert.equal(badges[0].tagName, 'SPAN');
    assert.equal(badges[0].getAttribute('aria-hidden'), 'true');
    assert.equal(h.header.getAttribute('data-neko-tooltip'), 'A question waits');

    h.parts.handleNekoPowerAskWaitingChange(false);
    assert.equal(h.shell.hasAttribute('data-nekopower-ask-waiting'), false);
    assert.equal(h.header.hasAttribute('data-neko-tooltip'), false);
    // Waiting again: the same badge, not another beside it.
    h.parts.handleNekoPowerAskWaitingChange(true);
    assert.equal(h.badges().length, 1);
});

test('only what is true is taken as waiting', () => {
    const h = createHarness();
    for (const value of [undefined, null, 1, 'true', {}]) {
        h.parts.handleNekoPowerAskWaitingChange(value);
        assert.equal(h.parts.nekoPowerAskWaiting, false, String(value));
        assert.equal(h.shell.hasAttribute('data-nekopower-ask-waiting'), false, String(value));
    }
});

test('says so to the desktop shell once for each change, with the words its own ball can show', () => {
    const h = createHarness();
    h.parts.handleNekoPowerAskWaitingChange(true);
    h.parts.handleNekoPowerAskWaitingChange(true);
    h.parts.handleNekoPowerAskWaitingChange(false);
    h.parts.handleNekoPowerAskWaitingChange(false);
    assert.deepEqual(JSON.parse(JSON.stringify(h.emitted)), [
        { type: 'react-chat-window:nekopower-ask-waiting-change', detail: { waiting: true, label: 'A question waits' } },
        { type: 'react-chat-window:nekopower-ask-waiting-change', detail: { waiting: false, label: '' } },
    ]);
});

test('the ball says what waits when hovered only while it is a ball', () => {
    const h = createHarness({ minimized: false, surfaceMode: 'compact' });
    h.parts.handleNekoPowerAskWaitingChange(true);
    // The compact chat floats the question itself: nothing is said on hovering it.
    assert.equal(h.header.hasAttribute('data-neko-tooltip'), false);
    assert.equal(h.shell.getAttribute('data-nekopower-ask-waiting'), 'true');

    h.setSurface(true, 'minimized');
    h.parts.syncChatSurfaceModeUI();
    assert.equal(h.header.getAttribute('data-neko-tooltip'), 'A question waits');

    h.setSurface(false, 'compact');
    h.parts.syncChatSurfaceModeUI();
    assert.equal(h.header.hasAttribute('data-neko-tooltip'), false);
});

test('leaves a hover text it did not put there alone', () => {
    const h = createHarness({ minimized: false, surfaceMode: 'compact' });
    h.header.setAttribute('data-neko-tooltip', 'someone else');
    h.parts.handleNekoPowerAskWaitingChange(true);
    h.parts.handleNekoPowerAskWaitingChange(false);
    assert.equal(h.header.getAttribute('data-neko-tooltip'), 'someone else');
});

test('the host hands the React chat the callback that tells it', () => {
    assert.match(
        GEOMETRY_SOURCE,
        /onNekoPowerAskWaitingChange:\s*I\.handleNekoPowerAskWaitingChange,/
    );
});

function cssRule(selector) {
    const at = CSS_SOURCE.indexOf(selector + ' {');
    assert.ok(at >= 0, `missing CSS rule: ${selector}`);
    return CSS_SOURCE.slice(at, CSS_SOURCE.indexOf('}', at));
}

test('the badge is drawn only on the ball of a chat that has a question waiting, and takes no pointer', () => {
    const hidden = cssRule('body.subtitle-web-host #react-chat-window-shell .react-chat-minimized-ask-badge');
    assert.match(hidden, /display:\s*none;/);
    const shown = cssRule(
        'body.subtitle-web-host #react-chat-window-shell.is-minimized[data-nekopower-ask-waiting="true"] .react-chat-minimized-ask-badge'
    );
    assert.match(shown, /display:\s*block;/);
    assert.match(shown, /position:\s*absolute;/);
    // Clicks and drags fall through to the ball under it.
    assert.match(shown, /pointer-events:\s*none;/);
});
