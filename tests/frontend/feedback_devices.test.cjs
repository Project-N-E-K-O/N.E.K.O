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
async function fixture() {
    const elements = Object.fromEntries(['status','upgrade','device-controls','device-name','replacement','replacement-id','activate','refresh','devices'].map(id => [id, new Element()]));
    const data = { owner: 'account-a', upgrade: false, calls: [], devices: Array.from({ length: 5 }, (_, i) => ({
        registration_id: 'old-' + i, registration_version: 1, display_name: 'Laptop ' + i, active: true, is_current: false })) };
    const fetch = async (url, options = {}) => {
        data.calls.push({ url, options });
        let value;
        if (url.endsWith('page_config')) value = { autostart_csrf_token: 'fixture-csrf' };
        else if (url.endsWith('/status')) value = { success: true, owner_key: data.owner, upgrade_required: data.upgrade };
        else if (url.endsWith('/devices')) value = { success: true, owner_key: data.owner, devices: data.devices, max_active_devices: 5 };
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
    const window = { t: key => key, addEventListener() {} };
    vm.runInNewContext(fs.readFileSync(path.join(root, 'static/feedback-devices.js'), 'utf8'), {
        window, document: { getElementById: id => elements[id], createElement: () => new Element() }, fetch,
    });
    await settle();
    return { elements, data };
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
    assert.deepEqual(JSON.parse(call.options.body), { expected_owner_key: 'account-a', replace_registration_id: 'old-3', display_name: 'My desktop' });
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

test('management copy has the same complete keys in all eight locales', () => {
    let expected;
    for (const locale of ['en','ja','ko','zh-CN','zh-TW','ru','pt','es']) {
        const entries = JSON.parse(fs.readFileSync(path.join(root, 'static/locales', locale + '.json'), 'utf8')).feedbackDevices;
        assert.ok(entries && Object.values(entries).every(value => typeof value === 'string' && value.length));
        const keys = Object.keys(entries).sort();
        if (expected) assert.deepEqual(keys, expected); else expected = keys;
    }
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
