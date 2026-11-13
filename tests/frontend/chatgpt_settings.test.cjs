const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function surface(initial, respond = () => ({}), browser = false) {
    const nodes = Object.fromEntries(['account', 'status', 'login', 'add', 'logout', 'authorization'].map(id => [id, {
        value: '', options: [], disabled: false, textContent: '', listeners: {},
        hidden: true, removeAttribute(name) { delete this[name]; },
        replaceChildren() { this.options = []; },
        appendChild(option) { this.options.push(option); },
        addEventListener(event, fn) { this.listeners[event] = fn; }
    }]));
    let state = initial;
    let invalidations = 0;
    let opened = '';
    let timer;
    const calls = [];
    const window = {
        listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; },
        open: () => null,
        electronShell: { openExternal: async url => { opened = url; } }
    };
    if (browser) delete window.electronShell;
    const context = {
        window, URL,
        document: {
            getElementById: id => nodes[id.replace('chatgpt-', '')],
            createElement: () => ({value: '', textContent: ''})
        },
        invalidateModelLists: () => invalidations++,
        setTimeout: fn => { timer = fn; return 1; }, clearTimeout: () => { timer = undefined; },
        fetch: async (url, options) => {
            calls.push({url, options});
            if (url.endsWith('/status')) {
                if (state instanceof Error) throw state;
                if (typeof state === 'function') return state();
                return {ok: true, json: async () => state};
            }
            const data = respond(url, JSON.parse(options.body));
            return {ok: !data.detail, json: async () => data};
        }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/chatgpt_settings.js'), 'utf8'), context);
    return {nodes, calls, window, setState: value => { state = value; },
        get opened() { return opened; }, get timer() { return timer; },
        get invalidations() { return invalidations; }};
}
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const signedIn = {accounts: [{id: 'account-12345678', email: 'demo@example.test'}], active: 'account-12345678', login_status: 'idle', error: ''};

test('active account labels use text content and status requests are local/no-store', async () => {
    const ui = surface({...signedIn, accounts: [{id: signedIn.active, email: '<img src=x onerror=alert(1)>'}]});
    await flush();
    assert.match(ui.nodes.account.options[0].textContent, /<img.*12345678.*✓/);
    assert.equal(ui.nodes.account.value, signedIn.active);
    assert.equal(ui.calls[0].options.cache, 'no-store');
    assert.equal(ui.calls[0].options.headers['X-Neko-ChatGPT'], '1');
    assert.match(ui.nodes.status.textContent, /connected/);
});

test('login opens official authorization, disables mutations while pending, and refreshes model lists', async () => {
    const ui = surface(signedIn, () => ({url: 'https://auth.openai.com/api/accounts/authorize?state=test'}));
    await flush();
    ui.setState({...signedIn, login_status: 'pending'});
    await ui.nodes.login.listeners.click();
    assert.match(ui.opened, /^https:\/\/auth.openai.com\/api\/accounts\/authorize/);
    assert.equal(JSON.parse(ui.calls.find(c => c.url.endsWith('/login')).options.body).account_id, signedIn.active);
    assert.equal(ui.nodes.add.disabled, true);
    assert.equal(ui.nodes.logout.disabled, true);
    const before = ui.invalidations;
    ui.setState({...signedIn, login_status: 'connected'});
    await ui.timer();
    await flush();
    assert.equal(ui.nodes.login.disabled, false);
    assert.ok(ui.invalidations > before);
});

test('untrusted authorization URLs never open a browser', async () => {
    const ui = surface(signedIn, () => ({url: 'https://evil.test/authorize'}));
    await flush();
    await ui.nodes.login.listeners.click();
    assert.equal(ui.opened, '');
    assert.match(ui.nodes.status.textContent, /failed/);
    assert.equal(ui.nodes.login.disabled, false);
});

test('logout preserves the remote revocation warning and restores controls', async () => {
    const ui = surface(signedIn, () => ({success: true, revocation_confirmed: false}));
    await flush();
    ui.setState({...signedIn, active: null});
    await ui.nodes.logout.listeners.click();
    assert.match(ui.nodes.status.textContent, /revocation/);
    assert.equal(ui.nodes.login.disabled, false);
    assert.equal(JSON.parse(ui.calls.find(c => c.url.endsWith('/logout')).options.body).account_id, signedIn.active);
});

test('status failures retry and restore controls after login completes or expires', async () => {
    for (const terminal of ['connected', 'expired']) {
        const ui = surface({...signedIn, login_status: 'pending'});
        await flush();
        ui.setState(new Error('temporary outage'));
        await ui.timer();
        assert.equal(ui.nodes.login.disabled, true);
        assert.equal(typeof ui.timer, 'function');
        ui.setState({...signedIn, login_status: terminal});
        await ui.timer();
        assert.equal(ui.nodes.login.disabled, false);
        assert.equal(ui.timer, undefined);
    }
});

test('blocked popup exposes safe authorization link until terminal status', async () => {
    const url = 'https://auth.openai.com/api/accounts/authorize?state=test';
    const ui = surface(signedIn, () => ({url}), true);
    await flush();
    ui.setState(new Error('offline'));
    await ui.nodes.login.listeners.click();
    assert.equal(ui.nodes.authorization.href, url);
    assert.equal(ui.nodes.authorization.hidden, false);
    assert.equal(ui.nodes.login.disabled, true);
    ui.setState({...signedIn, login_status: 'expired'});
    await ui.timer();
    assert.equal(ui.nodes.authorization.hidden, true);
    assert.equal(ui.nodes.authorization.href, undefined);
    assert.equal(ui.nodes.login.disabled, false);
});

test('pagehide blocks in-flight rearming and pageshow restores polling', async () => {
    const ui = surface({...signedIn, login_status: 'pending'});
    await flush();
    let reject;
    ui.setState(() => new Promise((_, fail) => { reject = fail; }));
    const poll = ui.timer();
    ui.window.listeners.pagehide();
    reject(new Error('late failure'));
    await poll;
    assert.equal(ui.timer, undefined);
    ui.setState({...signedIn, login_status: 'connected'});
    ui.window.listeners.pageshow();
    await flush();
    assert.equal(ui.nodes.login.disabled, false);
});

test('initial status request retries without requiring a page reload', async () => {
    const ui = surface(new Error('503'));
    await flush();
    ui.setState(signedIn);
    await ui.timer();
    assert.equal(ui.nodes.account.value, signedIn.active);
});

test('named subscription probes carry the model and separate model results', async () => {
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/api_key_settings.js'), 'utf8');
    const snippet = source.slice(source.indexOf('function buildConnectivityCacheId('), source.indexOf('function showMaskedSecretConnectivityNotice('));
    const nodes = Object.fromEntries(['conversation', 'vision'].flatMap(type => [
        [`${type}ModelProvider`, {value: 'chatgpt'}],
        [`${type}ModelId`, {value: `${type}-model`}],
    ]));
    const calls = [];
    const context = {
        AbortController, setTimeout, clearTimeout, console: {log() {}},
        document: {getElementById: id => nodes[id] || null},
        _assistApiProviders: {}, _coreApiProviders: {},
        getEffectiveAssistKey: () => '', getEffectiveAssistUrl: () => 'https://api.openai.com/v1',
        getProviderInfo: () => ({}), getProviderType: () => 'chatgpt',
        isMaskedSecretValue: () => false, rememberResolvedProviderUrl() {},
        fetch: async (_, init) => {
            calls.push(JSON.parse(init.body));
            return {ok: true, json: async () => ({success: true})};
        },
    };
    vm.runInNewContext(snippet + '\nglobalThis.manager = ConnectivityManager;', context);
    const manager = context.manager;
    const text = manager.resolveEffectiveKey({type: 'custom', modelType: 'conversation'});
    const vision = manager.resolveEffectiveKey({type: 'custom', modelType: 'vision'});
    assert.notEqual(text.cacheId, vision.cacheId);
    for (const resolved of [text, vision]) {
        assert.equal(resolved.model, resolved === text ? 'conversation-model' : 'vision-model');
        const result = await manager.testKey({provider_key: resolved.providerKey, provider_scope: resolved.providerScope,
            model: resolved.model, api_key: '', provider_type: resolved.providerType});
        assert.equal(result.success, true);
    }
    assert.deepEqual(calls.map(c => c.model), ['conversation-model', 'vision-model']);
    await manager.testKey({provider_key: 'openai', provider_scope: 'assist', model: 'ignored', api_key: 'test'});
    assert.equal('model' in calls[2], false);
});
