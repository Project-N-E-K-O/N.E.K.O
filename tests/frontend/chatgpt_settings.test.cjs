const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function surface(initial, respond = () => ({}), browser = false) {
    const nodes = Object.fromEntries(['account', 'status', 'login', 'add', 'logout', 'authorization', 'toggle-btn', 'options'].map(id => [id, {
        value: '', options: [], disabled: false, textContent: '', listeners: {},
        hidden: true, checked: false, style: {display: 'none'}, classList: {toggle() {}},
        setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; },
        replaceChildren() { this.options = []; },
        appendChild(option) { this.options.push(option); },
        addEventListener(event, fn) { this.listeners[event] = fn; },
        focus() { context.document.activeElement = this; }
    }]));
    let state = initial;
    let invalidations = 0;
    let connectivityInvalidations = 0;
    let opened = '';
    let timer;
    // Deadlines (AbortSignal.timeout) are expired by the test, not by wall-clock time.
    const deadlines = [];
    const calls = [];
    const window = {
        listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; },
        open: () => null, confirm: () => true,
        electronShell: { openExternal: async url => { opened = url; } }
    };
    if (browser) delete window.electronShell;
    const context = {
        window, URL,
        ConnectivityManager: {invalidateChatGPTAccount() { connectivityInvalidations++; }},
        document: {
            getElementById: id => nodes[id.replace('chatgpt-', '')],
            createElement: () => ({value: '', textContent: ''})
        },
        invalidateModelLists: () => invalidations++,
        AbortSignal: {timeout() {
            const listeners = [];
            const signal = {aborted: false, addEventListener: (name, fn) => listeners.push(fn),
                expire() { signal.aborted = true; listeners.forEach(fn => fn()); }};
            deadlines.push(signal);
            return signal;
        }},
        setTimeout: fn => { timer = fn; return 1; }, clearTimeout: () => { timer = undefined; },
        fetch: async (url, options) => {
            calls.push({url, options});
            if (url.endsWith('/status')) {
                if (state instanceof Error) throw state;
                if (typeof state === 'function') return state();
                return {ok: true, json: async () => state};
            }
            const data = await respond(url, JSON.parse(options.body));
            if (data.rawResponse) return data.rawResponse;
            return {ok: !data.detail, json: async () => data};
        }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/chatgpt_settings.js'), 'utf8'), context);
    return {nodes, calls, window, setState: value => { state = value; },
        get activeElement() { return context.document.activeElement; },
        get connectivityInvalidations() { return connectivityInvalidations; },
        get opened() { return opened; }, get timer() { return timer; },
        expireDeadlines: () => deadlines.splice(0).forEach(signal => signal.expire()),
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
    assert.equal(ui.calls[0].options.headers['Content-Type'], 'application/json');
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

test('a signed-out saved account stays chosen so it can sign in again', async () => {
    const accounts = [...signedIn.accounts, {id: 'other-87654321', email: 'b@example.test', connected: false}];
    const ui = surface({...signedIn, accounts}, () => ({url: 'https://auth.openai.com/api/accounts/authorize?state=x'}));
    await flush();
    ui.nodes.account.value = 'other-87654321';
    await ui.nodes.account.listeners.change();
    assert.equal(ui.calls.some(c => c.url.endsWith('/select')), false, 'selecting it would be refused');
    ui.window.listeners.localechange(); // any status refresh
    await flush();
    assert.equal(ui.nodes.account.value, 'other-87654321', 'a status refresh keeps the choice');
    await ui.nodes.login.listeners.click();
    const login = ui.calls.find(c => c.url.endsWith('/login'));
    assert.equal(JSON.parse(login.options.body).account_id, 'other-87654321');
});

test('adding another account after choosing a signed-out one shows the new account', async () => {
    const x = {id: 'signed-out-x-11111111', email: 'x@example.test', connected: false};
    const z = {id: 'added-z-22222222', email: 'z@example.test', connected: true};
    const ui = surface({...signedIn, accounts: [...signedIn.accounts, x]},
        () => ({url: 'https://auth.openai.com/api/accounts/authorize?state=add'}));
    await flush();
    ui.nodes.account.value = x.id;
    await ui.nodes.account.listeners.change();
    assert.equal(ui.nodes.account.value, x.id);
    ui.setState({...signedIn, accounts: [...signedIn.accounts, x], login_status: 'pending'});
    await ui.nodes.add.listeners.click();
    assert.equal(JSON.parse(ui.calls.find(c => c.url.endsWith('/login')).options.body).account_id, null);
    // The sign-in completes with Z as the active account; X is still signed out.
    ui.setState({accounts: [...signedIn.accounts, x, z], active: z.id, login_status: 'connected', error: ''});
    await ui.timer();
    assert.equal(ui.nodes.account.value, z.id);
});

test('a failed or expired re-authorization keeps the signed-out account chosen', async () => {
    for (const outcome of ['failed', 'expired']) {
        const b = {id: 'signed-out-b-33333333', email: 'b@example.test', connected: false};
        const ui = surface({...signedIn, accounts: [...signedIn.accounts, b]},
            () => ({url: 'https://auth.openai.com/api/accounts/authorize?state=retry'}));
        await flush();
        ui.nodes.account.value = b.id;
        await ui.nodes.account.listeners.change();
        ui.setState({...signedIn, accounts: [...signedIn.accounts, b], login_status: 'pending'});
        await ui.nodes.login.listeners.click();
        // The attempt ends without signing B in; A is still the active account.
        ui.setState({...signedIn, accounts: [...signedIn.accounts, b], login_status: outcome,
            error: outcome === 'failed' ? 'declined' : ''});
        await ui.timer();
        assert.equal(ui.nodes.account.value, b.id, `${outcome}: the retry still targets B`);
    }
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
    const nodes = Object.fromEntries(['conversation', 'recognition'].flatMap(type => [
        [`${type}ModelProvider`, {value: 'chatgpt'}],
        [`${type}ModelId`, {value: `${type}-model`}],
    ]));
    const calls = [];
    const context = {
        AbortController, setTimeout, clearTimeout, console: {log() {}},
        document: {getElementById: id => nodes[id] || null},
        _assistApiProviders: {}, _coreApiProviders: {},
        getEffectiveAssistKey: () => '', getEffectiveAssistUrl: () => 'https://api.openai.com/v1',
        getProviderInfo: () => ({}), getProviderType: () => 'chatgpt', followedModelType: () => '',
        isMaskedSecretValue: () => false, rememberResolvedProviderUrl() {},
        fetch: async (_, init) => {
            calls.push(JSON.parse(init.body));
            return {ok: true, json: async () => ({success: true})};
        },
    };
    vm.runInNewContext(snippet + '\nglobalThis.manager = ConnectivityManager;', context);
    const manager = context.manager;
    const text = manager.resolveEffectiveKey({type: 'custom', modelType: 'conversation'});
    const recognition = manager.resolveEffectiveKey({type: 'custom', modelType: 'recognition'});
    assert.notEqual(text.cacheId, recognition.cacheId);
    for (const resolved of [text, recognition]) {
        assert.equal(resolved.model, resolved === text ? 'conversation-model' : 'recognition-model');
        const result = await manager.testKey({provider_key: resolved.providerKey, provider_scope: resolved.providerScope,
            model: resolved.model, api_key: '', provider_type: resolved.providerType});
        assert.equal(result.success, true);
    }
    assert.deepEqual(calls.map(c => c.model), ['conversation-model', 'recognition-model']);
    await manager.testKey({provider_key: 'openai', provider_scope: 'assist', model: 'ignored', api_key: 'test'});
    assert.equal('model' in calls[2], false);
});

test('account invalidation clears only subscription lights and rejects even successful late tests', async () => {
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/api_key_settings.js'), 'utf8');
    const snippet = source.slice(source.indexOf('function buildConnectivityCacheId('), source.indexOf('function showMaskedSecretConnectivityNotice('));
    const nodes = {
        enableCustomApi: {checked: true}, conversationModelProvider: {value: 'chatgpt'},
        conversationModelId: {value: 'fixture-model'},
    };
    let complete;
    let signal;
    const context = {
        AbortController, setTimeout, clearTimeout, console: {log() {}},
        document: {getElementById: id => nodes[id] || null},
        _assistApiProviders: {}, _coreApiProviders: {}, CONNECTIVITY_TESTABLE_TYPES: ['conversation'],
        getEffectiveAssistKey: () => '', getEffectiveAssistUrl: () => 'https://api.openai.com/v1',
        getProviderInfo: () => ({}), getProviderType: () => 'chatgpt', followedModelType: () => '',
        isMaskedSecretValue: () => false, rememberResolvedProviderUrl() {},
        LightStatus: {UNTESTED: 'untested', CONNECTED: 'connected', TESTING: 'testing', FAILED: 'failed'},
        updateLightStatus: (light, status) => { light.status = status; },
        updateErrorMessage: (display, code, message) => { display.code = code; display.message = message; },
        fetch: async (_, init) => {
            signal = init.signal;
            return new Promise(resolve => { complete = () => resolve({ok: true, json: async () => ({success: true})}); });
        },
    };
    vm.runInNewContext(snippet + '\nglobalThis.manager = ConnectivityManager;', context);
    const manager = context.manager;
    const key = manager.resolveEffectiveKey({type: 'custom', modelType: 'conversation'}).cacheId;
    const unrelated = 'assist|openai|fixture';
    const light = {};
    const error = {};
    manager.keyLightsMap[key] = [light];
    manager.keyErrorDisplayMap[key] = [error];
    manager.keyStatusMap[key] = manager.keyStatusMap[unrelated] = 'connected';
    manager.keyErrorMap[key] = {error_code: 'old-account', error: 'old error'};
    manager.invalidateChatGPTAccount();
    assert.equal(light.status, 'untested');
    assert.equal(error.code, null);
    assert.equal(manager.keyStatusMap[unrelated], 'connected');
    const pending = manager.testCustomOnly();
    await flush();
    assert.equal(light.status, 'testing');
    manager.invalidateChatGPTAccount();
    assert.equal(signal.aborted, true);
    complete(); // Simulate a transport that resolves despite cancellation.
    await pending;
    assert.equal(manager.keyStatusMap[key], 'untested');
    assert.equal(manager.keyErrorMap[key], null);
    assert.equal(light.status, 'untested');
    assert.equal(manager.resolveEffectiveKey({type: 'custom', modelType: 'conversation'}).cacheId, key);
    manager.testKey = async () => {
        // Account changes after testKey resolves, before its caller writes state.
        queueMicrotask(() => manager.invalidateChatGPTAccount());
        return {success: true};
    };
    await manager.testCustomOnly();
    assert.equal(manager.keyStatusMap[key], 'untested');
});


test('disclosure starts collapsed and exposes its expanded state', async () => {
    const ui = surface(signedIn);
    await flush();
    assert.equal(ui.nodes.options.hidden, true);
    ui.nodes['toggle-btn'].listeners.click();
    assert.equal(ui.nodes.options.hidden, false);
    assert.equal(ui.nodes['toggle-btn']['aria-expanded'], 'true');
    ui.nodes['toggle-btn'].listeners.click();
    assert.equal(ui.nodes.options.hidden, true);
});

test('switching accounts invalidates connectivity again after the switch, even if the refresh fails', async () => {
    const ui = surface({...signedIn, accounts: [...signedIn.accounts, {id: 'other-87654321', email: 'b@example.test'}]},
        () => ({success: true}));
    await flush();
    const before = ui.connectivityInvalidations;
    ui.setState(new Error('offline'));
    ui.nodes.account.value = 'other-87654321';
    await ui.nodes.account.listeners.change();
    assert.equal(ui.connectivityInvalidations - before, 2);
});

test('uncertain login response rechecks server state even before local pending was observed', async () => {
    const ui = surface(signedIn, () => { throw new Error('response lost'); });
    await flush();
    ui.setState({...signedIn, login_status: 'pending'});
    await ui.nodes.login.listeners.click();
    assert.equal(typeof ui.timer, 'function');
    await ui.timer();
    assert.equal(ui.nodes.login.disabled, true);
    ui.setState({...signedIn, login_status: 'connected'});
    await ui.timer();
    assert.equal(ui.nodes.login.disabled, false);
});


test('non-JSON errors show a friendly failure instead of the parser message', async () => {
    const ui = surface(signedIn, () => ({rawResponse: {ok: false, json: async () => { throw new SyntaxError('Unexpected token I'); }}}));
    await flush();
    await ui.nodes.login.listeners.click();
    assert.match(ui.nodes.status.textContent, /connection failed/);
    assert.doesNotMatch(ui.nodes.status.textContent, /Unexpected token/);
});

test('account panel reuses page styles and has no subscription switch', () => {
    const root = path.join(__dirname, '../..');
    const html = fs.readFileSync(path.join(root, 'templates/api_key_settings.html'), 'utf8');
    const css = fs.readFileSync(path.join(root, 'static/css/api_key_settings.css'), 'utf8');
    assert.match(html, /id="chatgpt-settings" class="section"/);
    assert.match(html, /class="gsv-tutorial-link" href="https:\/\/chatgpt.com\/settings\/usage"/);
    assert.doesNotMatch(html, /chatgpt-enabled|chatgpt-confirm-modal|chatgpt-independent-settings-note/);
    assert.doesNotMatch(css, /#chatgpt-settings\s*\{[^}]*margin-bottom:/);
});
