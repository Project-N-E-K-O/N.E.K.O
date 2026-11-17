const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function surface(initial, respond = () => ({}), browser = false) {
    const nodes = Object.fromEntries(['account', 'status', 'login', 'add', 'logout', 'authorization', 'enabled', 'toggle-btn', 'options'].map(id => [id, {
        value: '', options: [], disabled: false, textContent: '', listeners: {},
        hidden: true, checked: false, classList: {toggle() {}},
        setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; },
        replaceChildren() { this.options = []; },
        appendChild(option) { this.options.push(option); },
        addEventListener(event, fn) { this.listeners[event] = fn; }
    }]));
    let state = initial;
    let subscription = { enabled: false, slots: ['conversation'], overwritten: ['conversation'], revision: 'fixture' };
    let reloads = 0;
    let invalidations = 0;
    let opened = '';
    let timer;
    const calls = [];
    const window = {
        listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; },
        open: () => null, confirm: () => true,
        electronShell: { openExternal: async url => { opened = url; } }
    };
    if (browser) delete window.electronShell;
    const context = {
        window, URL, loadCurrentApiKey: async () => { reloads++; },
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
            if (url.split('?')[0].endsWith('/subscription') && options.method === 'GET') {
                return {ok: true, json: async () => subscription};
            }
            const data = respond(url, JSON.parse(options.body));
            return {ok: !data.detail, json: async () => data};
        }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/chatgpt_settings.js'), 'utf8'), context);
    return {nodes, calls, window, setState: value => { state = value; },
        setSubscription: value => { subscription = {...subscription, ...value}; }, get reloads() { return reloads; },
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
    ui.window.confirm = () => false;
    ui.nodes.enabled.checked = true;
    await ui.nodes.enabled.listeners.change();
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
    let message;
    ui.window.confirm = text => { message = text; return true; };
    await flush();
    ui.nodes.enabled.checked = true;
    await ui.nodes.enabled.listeners.change();
    assert.match(message, /conversation/);
    assert.equal(ui.nodes.enabled.checked, true);
    assert.match(ui.nodes.status.textContent, /available-model/);
    assert.equal(ui.reloads, 1);
    ui.nodes.enabled.checked = false;
    await ui.nodes.enabled.listeners.change();
    assert.equal(ui.nodes.enabled.checked, false);
    assert.equal(ui.reloads, 2);
});

test('failed toggle reconciles persisted state and authorization warning keeps off available', async () => {
    const ui = surface(signedIn, () => ({detail: 'save failed'}));
    await flush();
    ui.nodes.enabled.checked = true;
    await ui.nodes.enabled.listeners.change();
    assert.equal(ui.nodes.enabled.checked, false);
    assert.match(ui.nodes.status.textContent, /save failed/);
    ui.setState({...signedIn, active: null, accounts: []});
    ui.setSubscription({enabled: true, authorization_required: true});
    ui.window.listeners.pageshow();
    await flush();
    assert.equal(ui.nodes.enabled.disabled, false);
    assert.match(ui.nodes.status.textContent, /restore/);
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
        _chatgptIndependentSlots: [], _loadedChatGPTIndependentFields: {},
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

test('ordinary full-form saves omit unchanged dormant slots including inherited display values', async () => {
    const values = {
        omniModelProvider: 'follow_core', omniModelUrl: 'wss://old.test/realtime',
        omniModelId: '', omniModelApiKey: '__NEKO_SECRET_MASKED__',
        ttsModelProvider: 'follow_assist', ttsModelUrl: 'https://old.test/tts',
        ttsModelId: '', ttsModelApiKey: '__NEKO_SECRET_MASKED__',
        imageModelProvider: 'disabled', imageModelUrl: '', imageModelId: '', imageModelApiKey: '',
    };
    const ui = independentSettingsSurface(values);
    ui.context.rememberChatGPTIndependentSettings(['omni', 'tts', 'image']);
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
    assert.equal(fullForm.imageModelProvider, 'disabled', 'the original confirmation payload stays unchanged');
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
