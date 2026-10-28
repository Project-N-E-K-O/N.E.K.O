'use strict';

// 「隐藏喵宇宙入口」开关：methods-buttons.js 里的 window.nekoCommunityEntry。
// 在 vm 里跑真实源码，验证持久化、按钮显隐和跨窗口同步。

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
const STORAGE_KEY = 'neko.communityEntry.hidden';

function loadCommunityEntry({ stored } = {}) {
    const storage = new Map();
    if (stored !== undefined) storage.set(STORAGE_KEY, stored);
    const listeners = new Map();
    const events = [];
    const wrappers = [{ style: { display: 'flex' } }, { style: { display: 'flex' } }];
    class CustomEvent {
        constructor(type, init = {}) {
            this.type = type;
            this.detail = init.detail;
        }
    }
    const window = {
        CustomEvent,
        localStorage: {
            getItem(key) { return storage.has(key) ? storage.get(key) : null; },
            setItem(key, value) { storage.set(key, String(value)); }
        },
        addEventListener(type, listener) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(listener);
        },
        dispatchEvent(event) {
            events.push(event);
            return true;
        }
    };
    const document = {
        querySelectorAll(selector) {
            if (selector === '[data-community-entry="true"]') return wrappers;
            return [];
        }
    };
    const context = vm.createContext({
        AvatarButtonMixin: { methods: {} },
        clearTimeout,
        console,
        CustomEvent,
        document,
        setTimeout,
        window
    });
    vm.runInContext(METHODS_SOURCE, context, { filename: 'methods-buttons.js' });
    return {
        api: window.nekoCommunityEntry,
        storage,
        wrappers,
        events,
        fireStorage(newValue) {
            (listeners.get('storage') || []).forEach((fn) => fn({ key: STORAGE_KEY, newValue }));
        }
    };
}

test('community entry is shown by default and reads the stored preference', () => {
    assert.equal(loadCommunityEntry().api.isHidden(), false);
    assert.equal(loadCommunityEntry({ stored: 'false' }).api.isHidden(), false);
    assert.equal(loadCommunityEntry({ stored: 'true' }).api.isHidden(), true);
});

test('hiding persists, hides every social wrapper and notifies once', () => {
    const { api, storage, wrappers, events } = loadCommunityEntry();

    assert.equal(api.setHidden(true), true);
    assert.equal(storage.get(STORAGE_KEY), 'true');
    assert.deepEqual(wrappers.map((w) => w.style.display), ['none', 'none']);
    assert.equal(events.length, 1);
    assert.equal(events[0].type, 'neko-community-entry-visibility-changed');
    assert.equal(events[0].detail.hidden, true);

    // 重复设置同一个值不再重复通知
    api.setHidden(true);
    assert.equal(events.length, 1);

    api.setHidden(false);
    assert.equal(storage.get(STORAGE_KEY), 'false');
    assert.deepEqual(wrappers.map((w) => w.style.display), ['flex', 'flex']);
    assert.equal(events.length, 2);
    assert.equal(events[1].detail.hidden, false);
});

test('a change written by another window is applied without writing storage back', () => {
    const harness = loadCommunityEntry();
    const { api, storage, wrappers, events } = harness;
    let writes = 0;
    const originalSetItem = harness.storage.set.bind(harness.storage);
    storage.set = (key, value) => { writes += 1; return originalSetItem(key, value); };

    harness.fireStorage('true');

    assert.equal(api.isHidden(), true);
    assert.equal(writes, 0);
    assert.deepEqual(wrappers.map((w) => w.style.display), ['none', 'none']);
    assert.equal(events.at(-1).detail.hidden, true);

    harness.fireStorage('false');
    assert.equal(api.isHidden(), false);
    assert.deepEqual(wrappers.map((w) => w.style.display), ['flex', 'flex']);
});
