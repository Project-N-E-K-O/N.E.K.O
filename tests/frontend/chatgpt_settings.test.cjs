const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function surface(initial, respond = () => ({})) {
    const nodes = Object.fromEntries(['account', 'status', 'login', 'add', 'logout'].map(id => [id, {
        value: '', options: [], disabled: false, textContent: '', listeners: {},
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
        addEventListener() {},
        electronShell: { openExternal: async url => { opened = url; } }
    };
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
            if (url.endsWith('/status')) return {ok: true, json: async () => state};
            const data = respond(url, JSON.parse(options.body));
            return {ok: !data.detail, json: async () => data};
        }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/chatgpt_settings.js'), 'utf8'), context);
    return {nodes, calls, setState: value => { state = value; },
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
