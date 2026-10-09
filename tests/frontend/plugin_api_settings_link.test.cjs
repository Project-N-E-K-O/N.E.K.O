// API 设置页的「Plugin API 设置」入口必须走应用内窗口（neko_plugin_dashboard），
// 不能被 target="_blank" 外链拦截器甩给系统浏览器（electronShell.openExternal）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const SOURCE_PATH = path.join(PROJECT_ROOT, 'static', 'js', 'api_key_settings.js');
const TEMPLATE_PATH = path.join(PROJECT_ROOT, 'templates', 'api_key_settings.html');
const source = fs.readFileSync(SOURCE_PATH, 'utf8');
const template = fs.readFileSync(TEMPLATE_PATH, 'utf8');

function sourceBetween(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start + startMarker.length);
    assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
    assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
    return source.slice(start, end);
}

// 载入 openPluginApiSettingsInApp + DOMContentLoaded 注册块，并触发 DOMContentLoaded。
const BOOT_SLICE = sourceBetween(
    'function openPluginApiSettingsInApp(',
    'function resolveCoreApiKeyForSave(',
);

function createLink({ id = null, href = null, inAppWindow = false }) {
    return {
        id,
        dataset: inAppWindow ? { inAppWindow: 'true' } : {},
        attributes: href === null ? {} : { href },
        listeners: {},
        addEventListener(type, callback) {
            (this.listeners[type] ||= []).push(callback);
        },
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
        },
        click() {
            let prevented = false;
            const event = { preventDefault() { prevented = true; } };
            (this.listeners.click || []).forEach(callback => callback(event));
            return { prevented };
        },
    };
}

function createStubWindow({ electronShell = null, openOrFocusWindow = null } = {}) {
    const openCalls = [];
    const win = {
        location: { origin: 'http://127.0.0.1:50111', href: 'http://127.0.0.1:50111/api_key' },
        openCalls,
        open(url, name, features) {
            const opened = {
                closed: false,
                focusCalls: 0,
                location: {
                    href: '',
                    replace(target) { this.href = target; },
                },
                focus() { this.focusCalls += 1; },
            };
            openCalls.push({ url, name, features, window: opened });
            return opened;
        },
    };
    if (electronShell) win.electronShell = electronShell;
    if (openOrFocusWindow) win.openOrFocusWindow = openOrFocusWindow;
    return win;
}

function bootPage({ links = [], elements = new Map(), windowStub } = {}) {
    const win = windowStub || createStubWindow();
    const context = vm.createContext({
        console,
        URL,
        screen: { width: 1920, height: 1080 },
        document: {
            baseURI: 'http://127.0.0.1:50111/api_key',
            listeners: {},
            addEventListener(type, callback) {
                (this.listeners[type] ||= []).push(callback);
            },
            getElementById: id => elements.get(id) || null,
            querySelectorAll: () => links,
        },
        window: win,
    });
    vm.runInContext(BOOT_SLICE, context, { filename: SOURCE_PATH });
    (context.document.listeners.DOMContentLoaded || []).forEach(callback => callback());
    return { context, win };
}

const PLUGIN_LINK_HREF = '/api/agent/user_plugin/dashboard?page=model-api';

test('template marks the Plugin API link as an in-app window entry', () => {
    assert.match(template, /id="plugin-api-settings-link"/);
    assert.match(template, /data-in-app-window="true"/);
    assert.ok(
        template.includes(`href="${PLUGIN_LINK_HREF}"`),
        'Plugin API anchor should keep the dashboard model-api href',
    );
});

test('Plugin API link opens the in-app dashboard window instead of the system browser', () => {
    const externalCalls = [];
    const pluginLink = createLink({ id: 'plugin-api-settings-link', href: PLUGIN_LINK_HREF, inAppWindow: true });
    const externalLink = createLink({ href: 'https://myaccount.console.aliyun.com/overview' });
    const elements = new Map([['plugin-api-settings-link', pluginLink]]);
    const { win } = bootPage({
        links: [externalLink, pluginLink],
        elements,
        windowStub: createStubWindow({
            electronShell: { openExternal: url => externalCalls.push(url) },
        }),
    });

    const { prevented } = pluginLink.click();
    assert.ok(prevented, 'anchor default navigation should be prevented');
    assert.deepEqual(externalCalls, [], 'Plugin API entry must not go through shell.openExternal');
    assert.equal(win.openCalls.length, 1);

    const call = win.openCalls[0];
    assert.equal(call.name, 'neko_plugin_dashboard');
    const url = new URL(call.url);
    assert.equal(url.pathname, '/api/agent/user_plugin/dashboard');
    assert.equal(url.searchParams.get('page'), 'model-api');
    assert.equal(url.searchParams.get('yui_opener_origin'), 'http://127.0.0.1:50111');
    assert.ok(url.searchParams.get('v'), 'cache buster should be present');
    assert.match(call.features, /width=\d+,height=\d+/);
    assert.equal(win._openedWindows.neko_plugin_dashboard, call.window);
    assert.equal(call.window.focusCalls, 1);

    // 普通外链仍走系统浏览器
    externalLink.click();
    assert.deepEqual(externalCalls, ['https://myaccount.console.aliyun.com/overview']);
    assert.equal(win.openCalls.length, 1, 'external link must not open an in-app window');
});

test('reopening the Plugin API entry reuses and reloads the tracked dashboard window', () => {
    const pluginLink = createLink({ id: 'plugin-api-settings-link', href: PLUGIN_LINK_HREF, inAppWindow: true });
    const elements = new Map([['plugin-api-settings-link', pluginLink]]);
    const { win } = bootPage({ links: [pluginLink], elements });

    pluginLink.click();
    const first = win.openCalls[0].window;
    pluginLink.click();

    assert.equal(win.openCalls.length, 1, 'second click must not open another window');
    assert.ok(first.location.href, 'tracked window should be reloaded in place');
    const reloaded = new URL(first.location.href);
    assert.equal(reloaded.searchParams.get('page'), 'model-api');
    assert.ok(reloaded.searchParams.get('v'), 'reload should carry a cache buster');
});

test('openOrFocusWindow is preferred when common_dialogs.js is loaded', () => {
    const focusCalls = [];
    const pluginLink = createLink({ id: 'plugin-api-settings-link', href: PLUGIN_LINK_HREF, inAppWindow: true });
    const elements = new Map([['plugin-api-settings-link', pluginLink]]);
    const { win } = bootPage({
        links: [pluginLink],
        elements,
        windowStub: createStubWindow({
            openOrFocusWindow: (url, name, features, options) => {
                focusCalls.push({ url, name, features, options });
                return { closed: false, focus() {}, location: { replace() {} } };
            },
        }),
    });

    pluginLink.click();
    assert.equal(win.openCalls.length, 0);
    assert.equal(focusCalls.length, 1);
    assert.equal(focusCalls[0].name, 'neko_plugin_dashboard');
    // options 对象诞生于 vm realm，原型不同，逐字段断言而非 deepEqual
    assert.equal(focusCalls[0].options.navigateOnReuse, true);
    assert.equal(new URL(focusCalls[0].url).searchParams.get('page'), 'model-api');
});
