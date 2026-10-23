const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

class Element {
    constructor() { this.children = []; this.listeners = {}; this.value = ''; this.hidden = false; this.disabled = false; this.style = {}; }
    appendChild(child) { child.parent = this; this.children.push(child); }
    replaceChildren(...children) { this.children = children; this.value = ''; }
    addEventListener(type, callback) { this.listeners[type] = callback; }
    click() { if (!this.disabled && this.listeners.click) this.listeners.click(); }
    setAttribute() {}
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
}
async function fixture(options = {}) {
    const elements = Object.fromEntries(['status','upgrade','device-controls','device-name','replacement','replacement-id','activate','refresh','devices'].map(id => [id, new Element()]));
    const events = {}, timeouts = [];
    const data = { owner: 'account-a', protocol: 2, upgrade: false, calls: [], devices: Array.from({ length: 5 }, (_, i) => ({
        registration_id: 'old-' + i, registration_version: 1, display_name: 'Laptop ' + i, active: true, is_current: false })) };
    const fetch = async (url, options = {}) => {
        data.calls.push({ url, options });
        if (options.signal && options.signal.aborted) throw Error('aborted');
        if (intercept) {
            const result = await intercept(url, options);
            if (result) return result;
        }
        let value;
        if (url.endsWith('page_config')) value = { autostart_csrf_token: 'fixture-csrf' };
        else if (url.endsWith('/status')) value = { success: true, owner_key: data.owner, protocol_version: data.protocol, upgrade_required: data.upgrade };
        else if (url.endsWith('/devices')) value = { success: true, owner_key: data.owner, devices: data.devices, max_active_devices: 5, activation_context: "synthetic-activation-context" };
        else if (url.endsWith('/activate')) {
            const body = JSON.parse(options.body);
            assert.equal(body.expected_owner_key, data.owner);
            const old = data.devices.find(row => row.registration_id === body.replace_registration_id);
            if (old) old.active = false;
            data.devices.push({ registration_id: 'current', registration_version: 1, display_name: body.display_name, active: true, is_current: true });
            value = { success: true };
        } else throw Error(url);
        return { ok: true, json: async () => JSON.parse(JSON.stringify(value)) };
    };
    const window = { t: key => key, addEventListener(name, callback) { events[name] = callback; } };
    let intercept = options.intercept;
    vm.runInNewContext(fs.readFileSync(path.join(root, 'static/feedback-devices.js'), 'utf8'), {
        window, document: { getElementById: id => elements[id], createElement: () => new Element() }, fetch,
        AbortSignal: { timeout(ms) { const controller = new AbortController(); timeouts.push({ms,controller}); return controller.signal; } },
    });
    await settle();
    return { elements, data, events, timeouts, setIntercept(value) { intercept = value; } };
}
async function settle() { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); }

test('sixth device requires an explicit choice and a click; names are only user supplied', async () => {
    const { elements, data } = await fixture();
    assert.equal(elements.activate.disabled, true);
    assert.equal(elements.replacement.hidden, false);
    assert.equal(data.calls.filter(row => row.url.endsWith('/activate')).length, 0);
    elements['replacement-id'].value = 'old-3';
    elements['replacement-id'].listeners.change();
    assert.equal(elements.activate.disabled, false);
    elements['device-name'].value = '  My desktop  ';
    elements.activate.click();
    await settle();
    const call = data.calls.find(row => row.url.endsWith('/activate'));
    assert.deepEqual(JSON.parse(call.options.body), { expected_owner_key: 'account-a', activation_context: 'synthetic-activation-context', replace_registration_id: 'old-3', expected_replace_registration_version: 1, display_name: 'My desktop' });
    assert.equal(call.options.headers['X-CSRF-Token'], 'fixture-csrf');
    assert.equal(data.devices.filter(row => row.active).length, 5);
    assert.equal(elements.activate.disabled, true);
});

test('upgrade state hides stale devices and keeps refresh available for recovery', async () => {
    const { elements, data } = await fixture();
    data.upgrade = true;
    elements.refresh.click();
    await settle();
    assert.equal(elements.upgrade.hidden, false);
    assert.equal(elements.devices.children.length, 0);
    assert.equal(elements.activate.disabled, true);
    assert.equal(elements.refresh.disabled, false);
    data.upgrade = false;
    elements.refresh.click();
    await settle();
    assert.equal(elements.upgrade.hidden, true);
    assert.equal(elements.devices.children.length, 5);
    assert.equal(data.calls.filter(row => row.url.endsWith('/activate')).length, 0);
});

test('replacement pins the viewed version and a changed device requires a fresh selection', async () => {
    const s = await fixture();
    s.elements['replacement-id'].value = 'old-2'; s.elements['replacement-id'].listeners.change();
    let attempts = 0;
    s.setIntercept((url, options) => {
        if (!url.endsWith('/activate')) return null;
        attempts++;
        const body = JSON.parse(options.body);
        if (attempts === 1) {
            assert.equal(body.expected_replace_registration_version, 1);
            s.data.devices[2].registration_version = 3;
            return {ok:false,status:409,json:async()=>({error_code:'cloud_conflict'})};
        }
        assert.equal(body.expected_replace_registration_version, 3);
        return null;
    });
    s.elements.activate.click(); await settle();
    assert.equal(attempts, 1);
    assert.equal(s.elements.status.textContent, 'feedbackDevices.conflict');
    assert.equal(s.elements['replacement-id'].value, '');
    assert.equal(s.elements.activate.disabled, true);
    assert.equal(s.data.devices[2].active, true);
    s.elements.activate.click(); await settle();
    assert.equal(attempts, 1);
    s.elements['replacement-id'].value = 'old-2'; s.elements['replacement-id'].listeners.change();
    s.elements.activate.click(); await settle();
    assert.equal(attempts, 2);
    assert.equal(s.data.devices[2].active, false);
});

test('refresh preserves a replacement choice only for the same owner and version', async () => {
    const s = await fixture();
    s.elements['replacement-id'].value = 'old-1'; s.elements['replacement-id'].listeners.change();
    s.elements.refresh.click(); await settle();
    assert.equal(s.elements['replacement-id'].value, 'old-1');
    s.data.owner = 'account-b';
    s.elements.refresh.click(); await settle();
    assert.equal(s.elements['replacement-id'].value, '');
    assert.equal(s.elements.activate.disabled, true);
});

test('management copy has the same complete keys in all eight locales', () => {
    let expected;
    for (const locale of ['en','ja','ko','zh-CN','zh-TW','ru','pt','es']) {
        const entries = JSON.parse(fs.readFileSync(path.join(root, 'static/locales', locale + '.json'), 'utf8')).feedbackDevices;
        assert.ok(entries && Object.values(entries).every(value => typeof value === 'string' && value.length));
        assert.equal(typeof entries.notEnabled, 'string');
        const keys = Object.keys(entries).sort();
        if (expected) assert.deepEqual(keys, expected); else expected = keys;
    }
});

test('configuration and device requests are bounded and an aborted mutation unlocks controls', async () => {
    const s = await fixture();
    assert.ok(s.data.calls.every(row => row.options.signal), 'all fetches need an abort deadline');
    assert.ok(s.timeouts.every(row => row.ms > 0 && row.ms <= 15000));
    let pending;
    s.setIntercept((url, options) => {
        if (!url.endsWith('/activate')) return null;
        pending = options.signal;
        return new Promise((_, reject) => pending.addEventListener('abort', () => reject(Error('timeout')), {once:true}));
    });
    s.elements['replacement-id'].value = 'old-0';
    s.elements['replacement-id'].listeners.change();
    s.elements.activate.click(); await settle();
    assert.ok(s.elements.refresh.disabled);
    s.timeouts.find(row => row.controller.signal === pending).controller.abort();
    await settle();
    assert.equal(s.elements.refresh.disabled, false);
    assert.equal(s.elements.status.textContent, 'feedbackDevices.unavailable');
});

test('default v1 explains unavailable capability and does not request devices', async () => {
    const s = await fixture();
    const before = s.data.calls.filter(row => row.url.endsWith('/devices')).length;
    s.data.protocol = 1; s.elements.refresh.click(); await settle();
    assert.equal(s.elements['device-controls'].hidden, true);
    assert.equal(s.elements.status.textContent, 'feedbackDevices.notEnabled');
    assert.equal(s.elements.refresh.disabled, false);
    assert.equal(s.data.calls.filter(row => row.url.endsWith('/devices')).length, before);
});

test('failed mutation and failed follow-up read still leave a usable retry button', async () => {
    const s = await fixture();
    s.setIntercept(url => {
        if (url.endsWith('/activate') || url.endsWith('/status')) throw Error('offline');
        return null;
    });
    s.elements['replacement-id'].value = 'old-0';
    s.elements['replacement-id'].listeners.change();
    s.elements.activate.click(); await settle();
    assert.equal(s.elements.status.textContent, 'feedbackDevices.unavailable');
    assert.equal(s.elements.refresh.disabled, false);
    s.setIntercept(null); s.elements.refresh.click(); await settle();
    assert.equal(s.elements.status.textContent, 'feedbackDevices.ready');
});

test('device refresh recovers a token rotated by a backend restart', async () => {
    const s = await fixture();
    let configReads = 0;
    s.setIntercept((url, options) => {
        if (url.endsWith('page_config')) {
            configReads++;
            return {ok:true,status:200,json:async()=>({autostart_csrf_token:'restarted-csrf'})};
        }
        if (options.headers['X-CSRF-Token'] !== 'restarted-csrf') {
            return {ok:false,status:403,json:async()=>({error_code:'csrf_validation_failed'})};
        }
        return null;
    });
    s.elements.refresh.click(); await settle();
    assert.equal(s.elements.status.textContent, 'feedbackDevices.ready');
    assert.equal(configReads, 1);
    assert.equal(s.elements.refresh.disabled, false);
});

test('only a definite CSRF rejection retries a mutation, preserving its owner and selection', async () => {
    const s = await fixture();
    let attempts = 0, configReads = 0;
    const bodies = [];
    s.setIntercept((url, options) => {
        if (url.endsWith('page_config')) {
            configReads++;
            return {ok:true,json:async()=>({autostart_csrf_token:'new-token'})};
        }
        if (!url.endsWith('/activate')) return null;
        attempts++; bodies.push(JSON.parse(options.body));
        if (attempts === 1) return {ok:false,status:403,json:async()=>({error_code:'csrf_validation_failed'})};
        assert.equal(options.headers['X-CSRF-Token'], 'new-token');
        return null;
    });
    s.elements['replacement-id'].value = 'old-2'; s.elements['replacement-id'].listeners.change();
    s.elements.activate.click(); await settle();
    assert.equal(attempts, 2);
    assert.equal(configReads, 1);
    assert.deepEqual(bodies, [
        {expected_owner_key:'account-a',activation_context:'synthetic-activation-context',replace_registration_id:'old-2',expected_replace_registration_version:1},
        {expected_owner_key:'account-a',activation_context:'synthetic-activation-context',replace_registration_id:'old-2',expected_replace_registration_version:1},
    ]);
});

for (const code of ['cloud_conflict', 'permission_denied']) {
    test(`a business 403 does not refresh credentials or retry a mutation: ${code}`, async () => {
        const s = await fixture(); let attempts = 0, configReads = 0;
        s.setIntercept(url => {
            if (url.endsWith('page_config')) configReads++;
            if (url.endsWith('/activate')) {
                attempts++;
                return {ok:false,status:403,json:async()=>({error_code:code})};
            }
            return null;
        });
        s.elements['replacement-id'].value = 'old-0'; s.elements['replacement-id'].listeners.change();
        s.elements.activate.click(); await settle();
        assert.equal(attempts, 1); assert.equal(configReads, 0);
        assert.equal(s.elements.refresh.disabled, false);
        assert.equal(s.data.devices.filter(row => row.active).length, 5);
    });
}

test('a persistent CSRF rejection retries at most once and leaves refresh available', async () => {
    const s = await fixture(); let attempts = 0;
    s.setIntercept(url => {
        if (!url.endsWith('/activate')) return null;
        attempts++;
        return {ok:false,status:403,json:async()=>({error_code:'csrf_validation_failed'})};
    });
    s.elements['replacement-id'].value = 'old-0'; s.elements['replacement-id'].listeners.change();
    s.elements.activate.click(); await settle();
    assert.equal(attempts, 2);
    assert.equal(s.elements.refresh.disabled, false);
    assert.equal(s.elements.status.textContent, 'feedbackDevices.unavailable');
});

test('teardown during token recovery prevents a stale mutation from retrying after pageshow', async () => {
    const s = await fixture(); let attempts = 0, finishConfig;
    s.setIntercept(url => {
        if (url.endsWith('/activate')) {
            attempts++;
            return {ok:false,status:403,json:async()=>({error_code:'csrf_validation_failed'})};
        }
        if (url.endsWith('page_config')) return new Promise(resolve => {finishConfig = resolve;});
        return null;
    });
    s.elements['replacement-id'].value = 'old-0'; s.elements['replacement-id'].listeners.change();
    s.elements.activate.click(); await settle();
    assert.equal(typeof finishConfig, 'function');
    s.events.pagehide(); s.setIntercept(null); s.data.owner = 'account-b'; s.events.pageshow(); await settle();
    finishConfig({ok:true,json:async()=>({autostart_csrf_token:'late-token'})}); await settle();
    assert.equal(attempts, 1, 'the old page owner cannot resubmit a control mutation');
    assert.equal(s.elements.status.textContent, 'feedbackDevices.ready');
});

test('device management resumes after BFCache and rejects a stale pre-hide response', async () => {
    const s = await fixture();
    let oldRead;
    s.setIntercept(url => url.endsWith('/status') ? new Promise(resolve => { oldRead = resolve; }) : null);
    s.elements.refresh.click(); await settle();
    s.events.pagehide(); s.setIntercept(null); s.data.owner = 'account-b';
    assert.equal(typeof s.events.pageshow, 'function');
    s.events.pageshow(); await settle();
    oldRead({ok:true,json:async()=>({success:true,owner_key:'account-a',protocol_version:1})});
    await settle();
    assert.equal(s.elements.status.textContent, 'feedbackDevices.ready');
    assert.equal(s.elements['device-controls'].hidden, false);
});

test('a late status response cannot hide a newer upgrade event; BFCache can refresh again', async () => {
    const events = {}, body = new Element();
    let finishRead, reads = 0;
    const window = {t:key=>key,addEventListener:(name,callback)=>{events[name]=callback;},
        nekoLocalMutationSecurity:{getMutationHeaders:async()=>({'X-CSRF-Token':'fixture'})}};
    vm.runInNewContext(fs.readFileSync(path.join(root,'static/feedback-upgrade-notice.js'),'utf8'), {
        window,document:{body,readyState:'complete',hidden:false,createElement:()=>new Element()},
        AbortSignal:{timeout:()=>undefined},
        fetch:async()=>{reads++;return reads === 1 ? new Promise(resolve=>{finishRead=resolve;})
            : {ok:true,json:async()=>({success:true,upgrade_required:false})};},
    });
    await settle(); events['neko:feedback-upgrade-required']();
    finishRead({ok:true,json:async()=>({success:true,upgrade_required:false})}); await settle();
    assert.equal(body.children.length, 1, 'a newer upgrade event retains its notice');
    events.pagehide();
    assert.equal(typeof events.pageshow, 'function');
    events.pageshow(); await settle();
    assert.equal(reads, 2);
    assert.equal(body.children.length, 0);
});

test('shared upgrade notice is visible on the existing event and clears on verified focus read without a timer', async () => {
    const events = {}, body = new Element();
    let required = false, reads = 0;
    const window = { t: key => key, addEventListener: (name, callback) => { events[name] = callback; },
        nekoLocalMutationSecurity: { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'fixture' }) } };
    const document = { body, readyState: 'complete', hidden: false, createElement: () => new Element() };
    vm.runInNewContext(fs.readFileSync(path.join(root, 'static/feedback-upgrade-notice.js'), 'utf8'), {
        window, document, AbortSignal: { timeout: () => undefined },
        fetch: async () => { reads++; return { ok: true, json: async () => ({ success: true, upgrade_required: required }) }; },
    });
    await settle();
    assert.equal(body.children.length, 0);
    events['neko:feedback-upgrade-required']();
    assert.equal(body.children.length, 1);
    assert.equal(body.children[0].children[0].textContent, 'feedbackDevices.upgrade');
    events.localechange();
    assert.equal(body.children.length, 1);
    events.focus();
    await settle();
    assert.equal(body.children.length, 0);
    assert.equal(reads, 2);
    events.pagehide(); events.focus();
    await settle();
    assert.equal(reads, 2);
});
