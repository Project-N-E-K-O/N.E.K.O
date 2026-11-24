const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function surface(initial, respond = () => ({}), browser = false) {
    const nodes = Object.fromEntries(['account', 'status', 'login', 'add', 'logout', 'authorization', 'enabled', 'toggle-btn', 'options', 'confirm-modal', 'confirm-message', 'confirm-cancel', 'confirm-accept'].map(id => [id, {
        value: '', options: [], disabled: false, textContent: '', listeners: {},
        hidden: true, checked: false, style: {display: 'none'}, classList: {toggle() {}},
        setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; },
        replaceChildren() { this.options = []; },
        appendChild(option) { this.options.push(option); },
        addEventListener(event, fn) { this.listeners[event] = fn; },
        focus() { context.document.activeElement = this; }
    }]));
    let state = initial;
    let subscription = { enabled: false, slots: ['conversation'], overwritten: ['conversation'], revision: 'fixture' };
    let reloads = 0;
    let reloadWork;
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
        rebinds: 0, refreshCustomConnectivityLightBindings() { this.rebinds++; },
        abandoned: [], abandonApiSettingsLoad(generation) { this.abandoned.push(generation); },
        loadGeneration: 0, apiSettingsLoadGeneration() { return this.loadGeneration; },
        electronShell: { openExternal: async url => { opened = url; } }
    };
    if (browser) delete window.electronShell;
    const context = {
        window, URL, _apiSaveInProgress: false, loadCurrentApiKey: async () => { reloads++; return await reloadWork?.(); },
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
            if (url.split('?')[0].endsWith('/subscription') && options.method === 'GET') {
                return {ok: true, json: async () => subscription};
            }
            const data = await respond(url, JSON.parse(options.body));
            if (data.rawResponse) return data.rawResponse;
            return {ok: !data.detail, json: async () => data};
        }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/chatgpt_settings.js'), 'utf8'), context);
    return {nodes, calls, window, setState: value => { state = value; },
        setSubscription: value => { subscription = {...subscription, ...value}; }, get reloads() { return reloads; },
        setSaveBusy: value => { context._apiSaveInProgress = value; },
        setReloadWork: value => { reloadWork = value; },
        get activeElement() { return context.document.activeElement; },
        get connectivityInvalidations() { return connectivityInvalidations; },
        get opened() { return opened; }, get timer() { return timer; },
        expireDeadlines: () => deadlines.splice(0).forEach(signal => signal.expire()),
        get invalidations() { return invalidations; }};
}
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const signedIn = {accounts: [{id: 'account-12345678', email: 'demo@example.test'}], active: 'account-12345678', login_status: 'idle', error: ''};

async function toggle(ui, requested, accept = true) {
    ui.nodes.enabled.checked = requested;
    const action = ui.nodes.enabled.listeners.change();
    await flush();
    assert.equal(ui.nodes['confirm-modal'].style.display, 'flex');
    ui.nodes[accept ? 'confirm-accept' : 'confirm-cancel'].listeners.click();
    await action;
}

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

test('unchanged subscription polls preserve model pickers and authorization changes invalidate them', async () => {
    const ui = surface(signedIn);
    ui.setSubscription({enabled: true, authorization_required: false});
    await flush();
    assert.equal(ui.invalidations, 1);
    await ui.timer();
    await ui.timer();
    assert.equal(ui.invalidations, 1);
    ui.setSubscription({authorization_required: true});
    await ui.timer();
    assert.equal(ui.invalidations, 2);
    ui.setSubscription({authorization_required: false});
    await ui.timer();
    assert.equal(ui.invalidations, 3);
    ui.setState({...signedIn, accounts: [...signedIn.accounts, {id: 'second', connected: true}], active: 'second'});
    await ui.timer();
    assert.equal(ui.invalidations, 4);
    assert.equal(ui.connectivityInvalidations, 4);
    await ui.timer();
    assert.equal(ui.invalidations, 4);
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

test('an unconfirmed revocation and an out-of-date form are both reported on logout', async () => {
    const ui = surface(signedIn, () => ({success: true, revocation_confirmed: false, subscription_restored: true}));
    await flush();
    ui.setReloadWork(async () => false);
    ui.setState({...signedIn, active: null});
    await ui.nodes.logout.listeners.click();
    assert.match(ui.nodes.status.textContent, /revocation/);
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
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
        _chatgptIndependentSlots: [],
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


test('connectivity checks skip slots left dormant by the subscription', async () => {
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/api_key_settings.js'), 'utf8');
    const snippet = source.slice(source.indexOf('function buildConnectivityCacheId('), source.indexOf('function showMaskedSecretConnectivityNotice('));
    const nodes = {
        enableCustomApi: {checked: true},
        conversationModelProvider: {value: 'chatgpt'}, conversationModelId: {value: 'fixture-model'},
        ttsModelProvider: {value: 'custom'}, ttsModelId: {value: 'dormant-tts'},
        ttsModelUrl: {value: 'https://dormant-tts.test/v1'}, ttsModelApiKey: {value: 'dormant-key'},
    };
    const bodies = [];
    const context = {
        AbortController, setTimeout, clearTimeout, console: {log() {}},
        document: {getElementById: id => nodes[id] || null},
        _assistApiProviders: {}, _coreApiProviders: {}, CONNECTIVITY_TESTABLE_TYPES: ['conversation', 'tts'],
        // The settings response lists the slots the subscription left dormant.
        _chatgptIndependentSlots: ['tts'],
        getEffectiveAssistKey: () => '', getEffectiveAssistUrl: () => 'https://api.openai.com/v1',
        getProviderInfo: () => ({}), getProviderType: pk => (pk === 'chatgpt' ? 'chatgpt' : 'openai_compatible'),
        followedModelType: () => '', getRealKey: input => input.value,
        isMaskedSecretValue: () => false, rememberResolvedProviderUrl() {},
        LightStatus: {UNTESTED: 'untested', CONNECTED: 'connected', TESTING: 'testing', FAILED: 'failed'},
        updateLightStatus() {}, updateErrorMessage() {},
        fetch: async (_, init) => {
            bodies.push(JSON.parse(init.body));
            return {ok: true, json: async () => ({success: true})};
        },
    };
    vm.runInNewContext(snippet + '\nglobalThis.manager = ConnectivityManager;', context);
    await context.manager.testCustomOnly();
    assert.deepEqual(bodies.map(body => body.model), ['fixture-model']);
    assert.equal(JSON.stringify(bodies).includes('dormant'), false);
    // The pre-save check (testAll) collects custom slots with the same rule.
    const testAll = source.slice(source.indexOf('    async testAll() {'), source.indexOf('    async testCustomOnly() {'));
    assert.match(testAll, /if \(_chatgptIndependentSlots\.includes\(mt\)\) return;/);
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

test('subscription requires login; cancellation never saves', async () => {
    const ui = surface({...signedIn, active: null, accounts: []});
    await flush();
    assert.equal(ui.nodes.enabled.disabled, true);
    ui.setState(signedIn);
    ui.window.listeners.pageshow();
    await flush();
    assert.equal(ui.nodes.enabled.disabled, false);
    await toggle(ui, true, false);
    assert.equal(ui.nodes.enabled.checked, false);
    assert.equal(ui.calls.filter(c => c.options.method === 'POST').length, 0);
});

test('toggle confirms affected slots, saves once, reloads and reflects persisted state', async () => {
    let ui;
    ui = surface(signedIn, (url, body) => {
        assert.ok(url.endsWith('/subscription'));
        assert.equal(body.revision, 'fixture');
        ui.setSubscription({enabled: body.enabled, model: 'available-model'});
        return {success: true};
    });
    await flush();
    await toggle(ui, true);
    assert.match(ui.nodes['confirm-message'].textContent, /conversation/);
    assert.equal(ui.nodes.enabled.checked, true);
    assert.match(ui.nodes.status.textContent, /available-model/);
    assert.equal(ui.reloads, 1);
    await toggle(ui, false);
    assert.equal(ui.nodes.enabled.checked, false);
    assert.equal(ui.reloads, 2);
});

test('a saved toggle whose settings reload fails says so instead of reporting a synced form', async () => {
    let ui;
    ui = surface(signedIn, (url, body) => {
        ui.setSubscription({enabled: body.enabled, model: 'available-model'});
        return {success: true};
    });
    await flush();
    await toggle(ui, true);
    // A successful reload re-points the connectivity lights at the new slot keys.
    assert.equal(ui.window.rebinds, 1);
    assert.match(ui.nodes.status.textContent, /available-model/);
    await toggle(ui, false);
    ui.setReloadWork(async () => false);
    await toggle(ui, true);
    assert.equal(ui.reloads, 3);
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
    // The enabled-subscription poll keeps the warning instead of "enabled and saved".
    await ui.timer();
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
    // A failing poll shows its error and still the warning.
    ui.setState(new Error('status offline'));
    await ui.timer();
    assert.match(ui.nodes.status.textContent, /status offline/);
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
    ui.setState(signedIn);
    await ui.timer();
    // A successful reload from an ordinary save clears it too.
    ui.window.listeners['neko:api-settings-loaded']();
    await flush();
    assert.match(ui.nodes.status.textContent, /available-model/);
    ui.setReloadWork(async () => false);
    await toggle(ui, false);
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
    // With the subscription off no poll is scheduled: the event itself replaces the warning.
    ui.window.listeners['neko:api-settings-loaded']();
    await flush();
    assert.doesNotMatch(ui.nodes.status.textContent, /could not reload the settings/);
    ui.setReloadWork(async () => false);
    await toggle(ui, false);
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
    // And so does a later successful reload from the switch itself.
    ui.setReloadWork(async () => undefined);
    await toggle(ui, true);
    assert.doesNotMatch(ui.nodes.status.textContent, /could not reload the settings/);
});

test('a stalled settings reload after a toggle releases the controls and warns', async () => {
    let ui;
    ui = surface(signedIn, (url, body) => {
        ui.setSubscription({enabled: body.enabled, model: 'available-model'});
        return {success: true};
    });
    await flush();
    ui.setReloadWork(() => new Promise(() => {})); // /core_api never answers
    ui.nodes.enabled.checked = true;
    const action = ui.nodes.enabled.listeners.change();
    await flush();
    ui.nodes['confirm-accept'].listeners.click();
    await flush();
    assert.equal(ui.window.isChatGPTSettingsBusy(), true);
    ui.expireDeadlines();
    await action;
    assert.equal(ui.window.isChatGPTSettingsBusy(), false);
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
    // The timed-out read is abandoned, so its late answer cannot land on later edits.
    assert.equal(ui.window.abandoned.length, 1);
});

test('a reload deadline abandons only its own unsettled read', async () => {
    let ui;
    ui = surface(signedIn, (url, body) => {
        ui.setSubscription({enabled: body.enabled, model: 'available-model'});
        return {success: true};
    });
    await flush();
    // The read finishes in time: its deadline expiring later must not touch a newer read.
    ui.window.loadGeneration = 7;
    await toggle(ui, true);
    ui.expireDeadlines();
    await flush();
    assert.deepEqual(ui.window.abandoned, []);
    // A stalled read is abandoned by the generation it was given, not "whatever is current".
    ui.window.loadGeneration = 8;
    ui.setReloadWork(() => new Promise(() => {}));
    ui.nodes.enabled.checked = false;
    const action = ui.nodes.enabled.listeners.change();
    await flush();
    ui.nodes['confirm-accept'].listeners.click();
    await flush();
    ui.window.loadGeneration = 9; // a newer read has started meanwhile
    ui.expireDeadlines();
    await action;
    assert.deepEqual(ui.window.abandoned, [8]);
});

test('the stale-form warning follows every other status it meets', async () => {
    let ui;
    ui = surface(signedIn, (url, body) => {
        ui.setSubscription({enabled: body.enabled, model: 'available-model'});
        return {success: true};
    });
    await flush();
    // The status read right after the change fails: its error and the warning both show.
    ui.setReloadWork(async () => { ui.setState(new Error('status offline')); return false; });
    await toggle(ui, true);
    assert.match(ui.nodes.status.textContent, /status offline/);
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
    // A pending sign-in or a lost authorization keeps it too.
    ui.setState({...signedIn, login_status: 'pending'});
    await ui.timer();
    assert.match(ui.nodes.status.textContent, /browser/);
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
    ui.setState(signedIn);
    ui.setSubscription({authorization_required: true});
    await ui.timer();
    assert.match(ui.nodes.status.textContent, /authorization is unavailable/);
    assert.match(ui.nodes.status.textContent, /could not reload the settings/);
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

test('failed toggle reconciles persisted state and authorization warning keeps off available', async () => {
    const ui = surface(signedIn, () => ({detail: 'save failed'}));
    await flush();
    await toggle(ui, true);
    assert.equal(ui.nodes.enabled.checked, false);
    assert.match(ui.nodes.status.textContent, /save failed/);
    ui.setState({...signedIn, active: null, accounts: []});
    ui.setSubscription({enabled: true, authorization_required: true});
    ui.window.listeners.pageshow();
    await flush();
    assert.equal(ui.nodes.enabled.disabled, false);
    assert.match(ui.nodes.status.textContent, /restore/);
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

test('non-JSON errors show a friendly failure and structured usage limits retain their message', async () => {
    for (const response of [
        {rawResponse: {ok: false, json: async () => { throw new SyntaxError('Unexpected token I'); }}},
        {detail: 'usageLimit'},
    ]) {
        const ui = surface(signedIn, () => response);
        await flush();
        await toggle(ui, true);
        assert.match(ui.nodes.status.textContent, response.detail ? /usageLimit/ : /connection failed/);
        assert.doesNotMatch(ui.nodes.status.textContent, /Unexpected token/);
        assert.equal(ui.reloads, 1, 'reload even after errors because the response may be lost after persistence');
    }
});

test('subscription controls reject mutations while ordinary save is in flight', async () => {
    const ui = surface(signedIn);
    await flush();
    const before = ui.calls.length;
    ui.setSaveBusy(true);
    for (const action of [
        () => ui.nodes.logout.listeners.click(),
        () => ui.nodes.login.listeners.click(),
        () => ui.nodes.account.listeners.change(),
        () => { ui.nodes.enabled.checked = true; return ui.nodes.enabled.listeners.change(); },
    ]) await action();
    assert.equal(ui.calls.length, before);
    assert.equal(ui.nodes.enabled.checked, false);
});

test('confirmation is cancel-safe, focus-managed and keeps the mutation lock until completion', async () => {
    for (const cancel of ['escape', 'backdrop', 'pagehide']) {
        const ui = surface(signedIn);
        await flush();
        ui.nodes.enabled.checked = true;
        const action = ui.nodes.enabled.listeners.change();
        await flush();
        assert.equal(ui.window.isChatGPTSettingsBusy(), true);
        assert.equal(ui.nodes['confirm-modal'].style.display, 'flex');
        assert.equal(ui.activeElement, ui.nodes['confirm-cancel']);
        let prevented = false;
        ui.nodes['confirm-modal'].listeners.keydown({key: 'Tab', preventDefault() {}});
        assert.equal(ui.activeElement, ui.nodes['confirm-accept']);
        ui.nodes['confirm-modal'].listeners.keydown({key: 'Tab', shiftKey: true, preventDefault() {}});
        assert.equal(ui.activeElement, ui.nodes['confirm-cancel']);
        if (cancel === 'escape') ui.nodes['confirm-modal'].listeners.keydown({key: 'Escape', preventDefault() { prevented = true; }});
        if (cancel === 'backdrop') ui.nodes['confirm-modal'].listeners.click({target: ui.nodes['confirm-modal']});
        if (cancel === 'pagehide') ui.window.listeners.pagehide();
        await action;
        assert.equal(ui.calls.filter(c => c.options.method === 'POST').length, 0);
        assert.equal(ui.nodes['confirm-modal'].style.display, 'none');
        assert.equal(ui.window.isChatGPTSettingsBusy(), false);
        assert.equal(ui.nodes.enabled.checked, false);
        if (cancel !== 'pagehide') assert.equal(ui.activeElement, ui.nodes.enabled);
        if (cancel === 'escape') assert.equal(prevented, true);
    }
});

test('uncertain logout reconciles persisted configuration before releasing the mutation lock', async () => {
    const ui = surface(signedIn, () => { throw new Error('logout response lost'); });
    await flush();
    ui.setState({...signedIn, active: null, accounts: []});
    await ui.nodes.logout.listeners.click();
    assert.equal(ui.reloads, 1);
    assert.equal(ui.nodes.account.value, '');
    assert.match(ui.nodes.status.textContent, /logout response lost/);
});

function independentSettingsSurface(values = {}) {
    const nodes = Object.fromEntries(Object.entries(values).map(([field, value]) => [field, {value, dataset: {}}]));
    nodes['chatgpt-independent-settings-note'] = {hidden: true};
    const calls = [];
    const messages = [];
    const window = {t: key => key};
    window.parent = window;
    const context = {
        window, Object, document: {
            getElementById: field => nodes[field],
            querySelectorAll: () => [],
        },
        MODEL_TYPES: ['conversation', 'omni', 'tts'], _imageProviders: {},
        _chatgptIndependentSlots: [], _loadedChatGPTIndependentFields: {}, _followClearedModelIds: new Set(),
        _apiSaveInProgress: false, getRealKey: input => input.value,
        showStatus: (message, status) => messages.push({message, status}),
        setApiSaveInProgress: value => { context._apiSaveInProgress = value; },
        runConnectivityCheckBeforeSave: async () => {}, refreshAutoResolvedModelUrlsForSave() {},
        invalidateModelLists() {}, cacheCurrentSecretDisplays() {}, clearVoiceIds: async () => {},
        getTargetOrigin: () => 'http://localhost', loadCurrentApiKey: async () => {},
        fetch: async (url, init) => {
            calls.push({url, body: JSON.parse(init.body)});
            return {ok: true, json: async () => ({success: true})};
        },
    };
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/api_key_settings.js'), 'utf8');
    const gsvStart = source.indexOf('function getGptSovitsConfigForSave(');
    vm.runInNewContext(source.slice(gsvStart, source.indexOf('// GPT-SoVITS「', gsvStart)), context);
    vm.runInNewContext(source.slice(source.indexOf('function imageSettingsPayload(')), context);
    vm.runInNewContext(source.slice(source.indexOf('function rememberChatGPTIndependentSettings('),
        source.indexOf('\nfunction showWarningModal(')), context);
    return {context, nodes, calls, messages};
}

test('ordinary save cannot race an in-flight subscription mutation or confirmation', async () => {
    const ui = independentSettingsSurface();
    ui.context.window.isChatGPTSettingsBusy = () => true;
    await ui.context.saveApiKey({enableCustomApi: true, coreApi: 'qwen', apiKey: 'fixture'});
    assert.equal(ui.calls.length, 0);
    assert.equal(ui.context._apiSaveInProgress, false);
});

test('ordinary save stays blocked through subscription persistence and settings reconciliation', async () => {
    let finishPost;
    let finishReload;
    const ui = surface(signedIn, () => new Promise(resolve => { finishPost = resolve; }));
    await flush();
    ui.setReloadWork(() => new Promise(resolve => { finishReload = resolve; }));
    const ordinary = independentSettingsSurface();
    ordinary.context.window.isChatGPTSettingsBusy = ui.window.isChatGPTSettingsBusy;
    const params = {enableCustomApi: true, coreApi: 'qwen', apiKey: 'fixture'};
    ui.nodes.enabled.checked = true;
    const action = ui.nodes.enabled.listeners.change();
    await flush();
    await ordinary.context.saveApiKey(params);
    assert.equal(ordinary.calls.length, 0, 'confirmation owns the mutation lock');
    ui.nodes['confirm-accept'].listeners.click();
    await flush();
    await ordinary.context.saveApiKey(params);
    assert.equal(ordinary.calls.length, 0, 'POST owns the mutation lock');
    finishPost({success: true});
    await flush();
    await ordinary.context.saveApiKey(params);
    assert.equal(ordinary.calls.length, 0, 'settings reload still owns the mutation lock');
    finishReload();
    await action;
    await ordinary.context.saveApiKey(params);
    assert.equal(ordinary.calls.length, 1);
});

test('subscription presentation reuses page styles and all locales provide title and usage-limit text', () => {
    const root = path.join(__dirname, '../..');
    const html = fs.readFileSync(path.join(root, 'templates/api_key_settings.html'), 'utf8');
    const css = fs.readFileSync(path.join(root, 'static/css/api_key_settings.css'), 'utf8');
    assert.match(html, /id="chatgpt-settings" class="section"/);
    assert.match(html, /id="chatgpt-confirm-modal" class="modal-overlay"/);
    assert.match(html, /role="dialog" aria-modal="true" aria-labelledby="chatgpt-confirm-title"/);
    assert.match(html, /class="gsv-tutorial-link" href="https:\/\/chatgpt.com\/settings\/usage"/);
    assert.doesNotMatch(html, /ChatGPT \/ Codex/);
    assert.doesNotMatch(css, /#chatgpt-settings\s*\{[^}]*margin-bottom:/);
    for (const locale of ['en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'es', 'pt', 'ru']) {
        const messages = JSON.parse(fs.readFileSync(path.join(root, `static/locales/${locale}.json`), 'utf8')).api.chatgpt;
        assert.match(messages.title, /ChatGPT/);
        assert.ok(messages.usageLimit.length > 15);
    }
});

test('ordinary full-form saves omit unchanged dormant slots including inherited display values', async () => {
    const values = {
        omniModelProvider: 'follow_core', omniModelUrl: 'wss://old.test/realtime',
        omniModelId: '', omniModelApiKey: '__NEKO_SECRET_MASKED__',
        ttsModelProvider: 'follow_assist', ttsModelUrl: 'https://old.test/tts',
        ttsModelId: '', ttsModelApiKey: '__NEKO_SECRET_MASKED__',
        imageModelProvider: 'disabled', imageModelUrl: '', imageModelId: '', imageModelApiKey: '',
        ttsProvider: 'legacy-voice-provider', ttsVoiceId: 'legacy-voice-id',
    };
    const ui = independentSettingsSurface(values);
    ui.context.rememberChatGPTIndependentSettings(['omni', 'tts', 'image'], values);
    assert.equal(ui.nodes['chatgpt-independent-settings-note'].hidden, false);
    ui.nodes.omniModelId.dataset = {fixedModelHidden: 'true'};
    const fullForm = {...values, enableCustomApi: true, coreApi: 'qwen', assistApi: 'qwen',
        conversationModelId: 'new-chat-model', disableTts: true,
        omniModelId: 'auto-filled-fixed-model',
        omniModelUrl: 'wss://updated-core.test/realtime', ttsModelApiKey: 'updated-book-key'};
    await ui.context.saveApiKey(fullForm);
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.calls[0].body.conversationModelId, 'new-chat-model');
    assert.equal(ui.calls[0].body.disableTts, true);
    for (const field of Object.keys(values)) assert.equal(Object.hasOwn(ui.calls[0].body, field), false);
    const changedVoice = {...fullForm, ttsVoiceId: 'different-voice'};
    await ui.context.saveApiKey(changedVoice);
    assert.equal(ui.calls.length, 1, 'a dormant legacy voice edit must be rejected');
    assert.equal(fullForm.imageModelProvider, 'disabled', 'the original confirmation payload stays unchanged');
});

test('untouched dormant voice settings never block a save, whatever legacy voice fields are stored', async () => {
    const cases = [
        // Stored GPT-SoVITS without a legacy ttsProvider: the form sends ttsProvider="gptsovits".
        {stored: {ttsVoiceId: 'gsv-voice'}, form: {ttsModelProvider: 'gptsovits', gptsovitsApiUrl: 'http://127.0.0.1:9881',
            gptsovitsVoiceId: 'gsv-voice', ttsVoiceId: ''}, submitted: {ttsProvider: 'gptsovits', ttsVoiceId: 'gsv-voice'}},
        // Custom TTS with a leftover ttsProvider="deepseek": the form sends ttsProvider="".
        {stored: {ttsProvider: 'deepseek', ttsVoiceId: 'saved-voice'}, form: {ttsModelProvider: 'custom',
            ttsVoiceId: 'saved-voice'}, submitted: {ttsProvider: '', ttsVoiceId: 'saved-voice'}},
    ];
    for (const {form, submitted} of cases) {
        const values = {ttsModelUrl: '', ttsModelId: '', ttsModelApiKey: '', ...form};
        const ui = independentSettingsSurface(values);
        ui.context.rememberChatGPTIndependentSettings(['tts']);
        const fullForm = {enableCustomApi: true, coreApi: 'qwen', conversationModelId: 'new-chat-model',
            ttsModelProvider: form.ttsModelProvider, ttsModelUrl: '', ttsModelId: '', ttsModelApiKey: '', ...submitted};
        if (form.ttsModelProvider === 'gptsovits') fullForm.ttsModelUrl = ui.context.getGptSovitsConfigForSave().url;
        await ui.context.saveApiKey(fullForm);
        assert.equal(ui.calls.length, 1, `untouched ${form.ttsModelProvider} voice settings must save`);
        assert.equal(ui.calls[0].body.conversationModelId, 'new-chat-model');
        // The stored legacy values stay as they are on the server.
        assert.equal(Object.hasOwn(ui.calls[0].body, 'ttsProvider'), false);
        assert.equal(Object.hasOwn(ui.calls[0].body, 'ttsVoiceId'), false);
        await ui.context.saveApiKey({...fullForm, ttsVoiceId: 'edited-voice'});
        assert.equal(ui.calls.length, 1, 'a real dormant voice edit is still rejected');
    }
});

test('a dormant slot that follows another tier is not edited when that tier changes', async () => {
    const values = {decisionModelProvider: 'follow_assist', decisionModelId: 'hand-picked-decision',
        decisionModelUrl: 'https://qwen.test/v1', decisionModelApiKey: '__NEKO_SECRET_MASKED__'};
    const ui = independentSettingsSurface(values);
    ui.context.MODEL_TYPES.push('decision');
    ui.context.rememberChatGPTIndependentSettings(['decision']);
    // Switching the assist provider clears follower model IDs (the real cascade
    // records the cleared field) and refreshes their URL.
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/api_key_settings.js'), 'utf8');
    const cascadeStart = source.indexOf('function clearFollowModelIdsOnApiChange(');
    vm.runInNewContext(source.slice(cascadeStart, source.indexOf('\nfunction ', cascadeStart + 1)), ui.context);
    ui.context._isLoadingSavedConfig = false;
    ui.context.MODEL_TYPES = ['decision'];
    ui.context.clearFollowModelIdsOnApiChange('follow_assist', 'qwen', 'openai');
    assert.equal(ui.nodes.decisionModelId.value, '');
    const fullForm = {enableCustomApi: true, coreApi: 'qwen', assistApi: 'openai',
        decisionModelProvider: 'follow_assist', decisionModelId: '', decisionModelUrl: 'https://openai.test/v1',
        decisionModelApiKey: '__NEKO_SECRET_MASKED__'};
    await ui.context.saveApiKey(fullForm);
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.calls[0].body.assistApi, 'openai');
    for (const field of Object.keys(values)) assert.equal(Object.hasOwn(ui.calls[0].body, field), false);
    // Picking another provider for the dormant slot is still refused.
    await ui.context.saveApiKey({...fullForm, decisionModelProvider: 'custom'});
    assert.equal(ui.calls.length, 1);
    // Typing into a cascade-cleared ID (then clearing it again) is the user's edit: the
    // model ID input listener drops the field from the cascade record.
    const listenerStart = source.indexOf("        ['ModelId', 'ModelUrl'].forEach(suffix => {");
    assert.ok(listenerStart > 0);
    assert.match(source.slice(listenerStart, listenerStart + 400),
        /_followClearedModelIds\.delete\(`\$\{modelType\}\$\{suffix\}`\)/);
    ui.context._followClearedModelIds.delete('decisionModelId');
    await ui.context.saveApiKey(fullForm);
    assert.equal(ui.calls.length, 1, 'a hand-cleared ID is refused once the record is dropped');
    // So is a model ID typed into it: refused with the explanation, never silently dropped.
    const before = ui.messages.length;
    await ui.context.saveApiKey({...fullForm, decisionModelId: 'typed-decision-model'});
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.messages.length, before + 1);
    assert.equal(ui.messages.at(-1).message, 'api.chatgpt.independentSettings');
    // Clearing the ID by hand, without switching the followed provider, is an edit too.
    const manual = independentSettingsSurface(values);
    manual.context.MODEL_TYPES.push('decision');
    manual.context.rememberChatGPTIndependentSettings(['decision']);
    await manual.context.saveApiKey({...fullForm, assistApi: 'qwen', decisionModelUrl: 'https://qwen.test/v1'});
    assert.equal(manual.calls.length, 0);
    assert.equal(manual.messages.at(-1).message, 'api.chatgpt.independentSettings');
});

test('ordinary saves use the same GSV defaults and unknown image serialization as the form', async () => {
    const ui = independentSettingsSurface({
        ttsModelProvider: 'gptsovits', ttsModelUrl: '', ttsModelId: '', ttsModelApiKey: '',
        gptsovitsApiUrl: '', gptsovitsVoiceId: '',
        imageModelProvider: 'unavailable-provider', imageModelUrl: ' https://image.test/v1 ',
        imageModelId: ' saved-model ', imageModelApiKey: '__NEKO_SECRET_MASKED__',
    });
    ui.context.rememberChatGPTIndependentSettings(['tts', 'image']);
    await ui.context.saveApiKey({
        enableCustomApi: true, coreApi: 'qwen', ttsModelProvider: 'gptsovits',
        ttsModelUrl: ui.context.getGptSovitsConfigForSave().url, ttsModelId: '', ttsModelApiKey: '',
        ...ui.context.imageSettingsPayload(),
    });
    assert.equal(ui.calls.length, 1);
    assert.equal(Object.hasOwn(ui.calls[0].body, 'ttsModelUrl'), false);
    assert.equal(Object.hasOwn(ui.calls[0].body, 'imageModelUrl'), false);
});

test('editing an independent slot is explained before saving and is allowed after restrictions end', async () => {
    for (const slot of ['image', 'omni', 'tts']) {
        const values = {[`${slot}ModelProvider`]: 'custom', [`${slot}ModelUrl`]: 'https://fixture.test/v1',
            [`${slot}ModelId`]: 'old-model', [`${slot}ModelApiKey`]: '__NEKO_SECRET_MASKED__'};
        const ui = independentSettingsSurface(values);
        ui.context.rememberChatGPTIndependentSettings([slot]);
        const edited = {...values, enableCustomApi: true, coreApi: 'qwen', [`${slot}ModelId`]: 'new-model'};
        await ui.context.saveApiKey(edited);
        assert.equal(ui.calls.length, 0);
        assert.deepEqual(ui.messages.at(-1), {message: 'api.chatgpt.independentSettings', status: 'error'});
        ui.context.rememberChatGPTIndependentSettings([]);
        assert.equal(ui.nodes['chatgpt-independent-settings-note'].hidden, true);
        await ui.context.saveApiKey(edited);
        assert.equal(ui.calls.length, 1);
        assert.equal(ui.calls[0].body[`${slot}ModelId`], 'new-model');
    }
});
