const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

class Element {
    constructor(ownerDocument) { this.ownerDocument = ownerDocument; this.children = []; this.listeners = {}; this.value = ''; this.hidden = false; this.disabled = false; this.style = {}; }
    appendChild(child) { child.parent = this; this.children.push(child); }
    contains(child) { return child === this || this.children.some(element => element.contains(child)); }
    focus(options) { this.ownerDocument.activeElement = this; this.focusOptions = options; }
    replaceChildren(...children) { this.children = children; this.value = ''; }
    addEventListener(type, callback) { this.listeners[type] = callback; }
    click() { if (!this.disabled && this.listeners.click) this.listeners.click(); }
    setAttribute() {}
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
}
async function fixture(options = {}) {
    const document = { getElementById: id => elements[id], createElement: () => new Element(document) };
    document.body = new Element(document);
    document.activeElement = document.body;
    const elements = Object.fromEntries(['status','upgrade','device-controls','device-name','replacement','replacement-id','activate','refresh','devices','coming-soon','coming-soon-title','management-content'].map(id => [id, new Element(document)]));
    for (const id of ['status', 'upgrade', 'refresh', 'device-controls']) elements['management-content'].appendChild(elements[id]);
    for (const id of ['device-name', 'replacement', 'activate', 'devices']) elements['device-controls'].appendChild(elements[id]);
    elements.replacement.appendChild(elements['replacement-id']);
    elements['coming-soon'].appendChild(elements['coming-soon-title']);
    const headerControl = new Element(document);
    const template = fs.readFileSync(path.join(root, 'templates/feedback_devices.html'), 'utf8');
    for (const id of ['coming-soon', 'management-content']) {
        const tag = template.match(new RegExp('<[^>]*\\bid="' + id + '"[^>]*>'));
        assert.ok(tag, `template must define ${id}`);
        elements[id].hidden = /\shidden(?:\s|=|>)/.test(tag[0]);
    }
    const events = {}, documentEvents = {}, timeouts = [], announcements = [];
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
        else if (url.endsWith('/status')) value = { success: true, owner_key: data.owner, protocol_version: data.protocol, upgrade_required: data.upgrade, ...data.status };
        else if (url.endsWith('/devices')) value = { success: true, owner_key: data.owner, devices: data.devices, max_active_devices: 5, activation_context: "synthetic-activation-context" };
        else if (url.endsWith('/activate')) {
            const body = JSON.parse(options.body);
            assert.equal(body.expected_owner_key, data.owner);
            const old = data.devices.find(row => row.registration_id === body.replace_registration_id);
            if (old) old.active = false;
            data.devices.push({ registration_id: 'current', registration_version: 1, display_name: body.display_name, active: true, is_current: true });
            value = { success: true };
        } else if (/\/devices\/[^/]+\/remove$/.test(url)) {
            const body = JSON.parse(options.body);
            const id = decodeURIComponent(url.split('/').at(-2));
            const device = data.devices.find(row => row.registration_id === id && row.active);
            assert.ok(device, 'remove must address an active registration');
            assert.equal(body.expected_owner_key, data.owner);
            assert.equal(body.expected_registration_version, device.registration_version);
            device.active = false;
            value = { success: true };
        } else throw Error(url);
        return { ok: true, json: async () => JSON.parse(JSON.stringify(value)) };
    };
    const window = { t: key => key, electronShell: options.electronShell,
        addEventListener(name, callback) { events[name] = callback; },
        dispatchEvent(event) { announcements.push(event); events[event.type]?.(event); } };
    document.hidden = false;
    document.addEventListener = (name, callback) => { documentEvents[name] = callback; };
    let intercept = options.intercept;
    vm.runInNewContext(fs.readFileSync(path.join(root, 'static/feedback-devices.js'), 'utf8'), {
        window, document, fetch, AbortController, CustomEvent,
        AbortSignal: { any: AbortSignal.any.bind(AbortSignal), timeout(ms) { const controller = new AbortController(); timeouts.push({ms,controller}); return controller.signal; } },
    }, { filename: path.join(root, 'static/feedback-devices.js') });
    await settle();
    return { elements, data, events, document, documentEvents, announcements, timeouts, headerControl, setIntercept(value) { intercept = value; } };
}
async function settle() { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); }

for (const outcome of ['notEnabled', 'ready', 'upgrade', 'unavailable']) {
    test(`initial status read hides management controls, then renders ${outcome}`, async () => {
        let releaseStatus, rejectStatus;
        const statusRead = new Promise((resolve, reject) => { releaseStatus = resolve; rejectStatus = reject; });
        const s = await fixture({ intercept: url => url.endsWith('/status') ? statusRead : undefined });
        assert.ok(s.data.calls.some(row => row.url.endsWith('/status')));
        assert.equal(s.elements['coming-soon'].hidden, true);
        assert.equal(s.elements['management-content'].hidden, true, 'management controls must stay hidden while capability is unknown');
        if (outcome === 'unavailable') rejectStatus(Error('offline'));
        else releaseStatus({ ok: true, json: async () => ({ success: true, owner_key: s.data.owner,
            protocol_version: outcome === 'notEnabled' ? 1 : 2, upgrade_required: outcome === 'upgrade' }) });
        await settle();
        assert.equal(s.elements['coming-soon'].hidden, outcome !== 'notEnabled');
        assert.equal(s.elements['management-content'].hidden, outcome === 'notEnabled');
        assert.equal(s.elements.status.textContent, 'feedbackDevices.' + outcome);
        assert.equal(s.elements.refresh.disabled, false);
        assert.equal(s.document.activeElement, s.document.body, 'initial status must not steal focus');
        if (outcome === 'ready') assert.equal(s.elements.devices.children.length, 5);
        if (outcome === 'upgrade') assert.equal(s.elements.upgrade.hidden, false);
    });
}

for (const target of ['refresh', 'device-name', 'remove']) {
    test(`coming-soon transition moves focus from ${target} to its visible heading`, async () => {
        const s = await fixture();
        const control = target === 'remove' ? s.elements.devices.children[0].children[1] : s.elements[target];
        control.focus();
        s.data.protocol = 1;
        s.elements.refresh.click();
        await settle();
        assert.equal(s.elements['management-content'].hidden, true);
        assert.equal(s.elements['coming-soon'].hidden, false);
        assert.equal(s.document.activeElement, s.elements['coming-soon-title']);
        assert.equal(s.elements['coming-soon-title'].focusOptions.preventScroll, true);
        const template = fs.readFileSync(path.join(root, 'templates/feedback_devices.html'), 'utf8');
        assert.match(template, /<h1\b[^>]*id="coming-soon-title"[^>]*tabindex="-1"/);
    });
}

test('focus moved to the header during a pending refresh is preserved, including locale changes', async () => {
    const s = await fixture();
    let releaseStatus;
    s.setIntercept(url => url.endsWith('/status') ? new Promise(resolve => { releaseStatus = resolve; }) : undefined);
    s.elements.refresh.focus();
    s.elements.refresh.click();
    await settle();
    assert.equal(typeof releaseStatus, 'function');
    s.headerControl.focus();
    releaseStatus({ ok: true, json: async () => ({ success: true, protocol_version: 1 }) });
    await settle();
    assert.equal(s.elements['coming-soon'].hidden, false);
    assert.equal(s.document.activeElement, s.headerControl);
    s.events.localechange();
    assert.equal(s.document.activeElement, s.headerControl);
});

for (const outcome of ['ready', 'upgrade', 'unavailable']) {
    test(`${outcome} refresh keeps focus on the visible refresh button`, async () => {
        const s = await fixture();
        s.elements.refresh.focus();
        if (outcome === 'upgrade') s.data.upgrade = true;
        if (outcome === 'unavailable') s.setIntercept(() => { throw Error('offline'); });
        s.elements.refresh.click();
        await settle();
        assert.equal(s.elements['management-content'].hidden, false);
        assert.equal(s.document.activeElement, s.elements.refresh);
    });
}

for (const outcome of ['ready', 'upgrade', 'unavailable', 'conflict', 'notEnabled']) {
    for (const focus of ['notice', 'header', 'body']) {
        test(`pageshow from coming-soon to ${outcome} preserves focus ownership: ${focus}`, async () => {
            const s = await fixture();
            s.elements.refresh.focus();
            s.data.protocol = 1;
            s.elements.refresh.click();
            await settle();
            assert.equal(s.document.activeElement, s.elements['coming-soon-title']);
            s.events.pagehide();
            let releaseStatus;
            s.setIntercept(url => url.endsWith('/status') ? new Promise(resolve => { releaseStatus = resolve; }) : undefined);
            s.events.pageshow();
            await settle();
            assert.equal(typeof releaseStatus, 'function');
            if (focus === 'header') s.headerControl.focus();
            if (focus === 'body') s.document.activeElement = s.document.body;
            const failed = outcome === 'unavailable' || outcome === 'conflict';
            releaseStatus({ ok: !failed, status: failed ? 503 : 200, json: async () => failed
                ? { success: false, error_code: outcome === 'conflict' ? 'cloud_conflict' : 'offline' }
                : { success: true, owner_key: s.data.owner, protocol_version: outcome === 'notEnabled' ? 1 : 2,
                    upgrade_required: outcome === 'upgrade' } });
            await settle();
            const expected = focus === 'header' ? s.headerControl : focus === 'body' ? s.document.body
                : outcome === 'notEnabled' ? s.elements['coming-soon-title'] : s.elements.refresh;
            assert.equal(s.elements['coming-soon'].hidden, outcome !== 'notEnabled');
            assert.equal(s.elements['management-content'].hidden, outcome === 'notEnabled');
            assert.equal(s.elements.status.textContent, 'feedbackDevices.' + outcome);
            assert.equal(s.document.activeElement, expected);
            assert.equal(s.elements.refresh.disabled, false);
            if (expected === s.elements.refresh) assert.equal(expected.focusOptions.preventScroll, true);
            s.events.localechange();
            assert.equal(s.document.activeElement, expected, 'retranslation must not move focus');
        });
    }
}

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

test('removing a rendered device pins its owner and version and refreshes retired controls', async () => {
    const s = await fixture();
    const device = s.data.devices[2];
    device.registration_id = 'desktop /测试#1';
    device.registration_version = 7;
    s.elements.refresh.click(); await settle();
    const button = s.elements.devices.children[2].children[1];
    const oldRemove = button.listeners.click;
    const before = s.data.calls.length;
    button.click(); await settle();
    const calls = s.data.calls.slice(before);
    const mutations = calls.filter(row => row.options.method === 'POST');
    assert.equal(mutations.length, 1);
    assert.equal(mutations[0].url, '/api/proactive/feedback/devices/desktop%20%2F%E6%B5%8B%E8%AF%95%231/remove');
    assert.deepEqual(JSON.parse(mutations[0].options.body), { expected_owner_key: 'account-a', expected_registration_version: 7 });
    assert.equal(mutations[0].options.headers['X-CSRF-Token'], 'fixture-csrf');
    assert.equal(device.active, false);
    assert.equal(s.data.devices.filter(row => row.active).length, 4);
    assert.ok(calls.some(row => row.url.endsWith('/status')));
    assert.ok(calls.some(row => row.url.endsWith('/devices')));
    const removed = s.elements.devices.children[2];
    assert.equal(removed.children.length, 1, 'the refreshed inactive device has no remove button');
    assert.equal(removed.children[0].textContent, 'Laptop 2 · feedbackDevices.removed');
    assert.equal(s.elements.replacement.hidden, true);
    assert.equal(s.elements.activate.disabled, false, 'removing a device frees an activation slot');
    assert.equal(s.elements.refresh.disabled, false);
    oldRemove(); await settle();
    assert.equal(s.data.calls.filter(row => row.options.method === 'POST').length, 1, 'the retired button cannot repeat the removal');
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

test('every refresh retires the previous replacement choice before reading', async () => {
    const s = await fixture();
    s.elements['replacement-id'].value = 'old-1'; s.elements['replacement-id'].listeners.change();
    s.elements.refresh.click(); await settle();
    assert.equal(s.elements['replacement-id'].value, '');
    s.data.owner = 'account-b';
    s.elements.refresh.click(); await settle();
    assert.equal(s.elements['replacement-id'].value, '');
    assert.equal(s.elements.activate.disabled, true);
});

test('focus and visible events combine one read and immediately retire old actions and replacement', async () => {
    const s = await fixture();
    s.elements['replacement-id'].value = 'old-1'; s.elements['replacement-id'].listeners.change();
    const oldRemove = s.elements.devices.children[0].children[1].listeners.click;
    const before = s.data.calls.length;
    let finishRead, signal;
    s.setIntercept((url, options) => url.endsWith('/status') ? new Promise(resolve => { finishRead = resolve; signal = options.signal; }) : null);
    s.events.focus(); s.documentEvents.visibilitychange();
    assert.equal(s.elements.devices.children.length, 0);
    assert.equal(s.elements['replacement-id'].value, '');
    assert.equal(s.elements.activate.disabled, true);
    oldRemove(); s.elements.activate.listeners.click();
    await settle();
    assert.equal(s.data.calls.slice(before).filter(row => row.url.endsWith('/status')).length, 1);
    assert.equal(s.data.calls.slice(before).filter(row => row.options.method === 'POST').length, 0);
    assert.equal(signal.aborted, false);
    finishRead({ok:true,json:async()=>({success:true,owner_key:s.data.owner,protocol_version:2})});
    await settle();
    assert.equal(s.elements.devices.children.length, 5);
    oldRemove(); await settle();
    assert.equal(s.data.calls.slice(before).filter(row => row.options.method === 'POST').length, 0, 'a retired callback cannot revive for the same owner');
    const after = s.data.calls.length;
    s.document.hidden = true; s.documentEvents.visibilitychange(); await settle();
    assert.equal(s.data.calls.length, after, 'hidden tabs do not refresh');
});

test('account-changing cancels a queued read and keeps old controls retired until account-changed', async () => {
    const s = await fixture(), before = s.data.calls.length;
    s.events.focus(); s.events['neko:account-changing']();
    s.events.focus(); s.documentEvents.visibilitychange();
    await settle();
    assert.equal(s.data.calls.length, before);
    assert.equal(s.elements.devices.children.length, 0);
    assert.equal(s.announcements.at(-1).detail, null);
    s.data.owner = 'account-b'; s.events['neko:account-changed'](); await settle();
    assert.equal(s.announcements.at(-1).detail.owner_key, 'account-b');
    assert.equal(s.elements.devices.children.length, 5);
});

test('A to B to A retires reads even when the transport ignores abort and responds late', async () => {
    const s = await fixture(); let oldStatus, oldDevices, statusSignal, devicesSignal;
    s.setIntercept((url, options) => url.endsWith('/status') ? new Promise(resolve => {oldStatus=resolve;statusSignal=options.signal;}) : null);
    s.events.focus(); await settle();
    s.setIntercept((url, options) => url.endsWith('/devices') ? new Promise(resolve => {oldDevices=resolve;devicesSignal=options.signal;}) : null);
    s.data.owner = 'account-b'; s.events['neko:account-changed'](); await settle();
    assert.equal(statusSignal.aborted, true);
    s.setIntercept(null); s.data.owner='account-a'; s.events['neko:account-changed'](); await settle();
    assert.equal(devicesSignal.aborted, true);
    const before = s.data.calls.length;
    oldStatus({ok:true,json:async()=>({success:true,owner_key:'account-a',protocol_version:1})});
    oldDevices({ok:true,json:async()=>({success:true,owner_key:'account-b',devices:[],max_active_devices:5})});
    await settle();
    assert.equal(s.data.calls.length, before, 'late status cannot initiate a follow-up devices read');
    assert.equal(s.elements.status.textContent, 'feedbackDevices.ready');
    assert.equal(s.elements.devices.children.length, 5);
    assert.equal(s.announcements.at(-1).detail.owner_key, 'account-a');
});

test('superseding a read aborts its transport without letting cancellation erase the replacement snapshot', async () => {
    const s=await fixture(); let aborted=false;
    s.setIntercept((url,options)=>url.endsWith('/status') ? new Promise((resolve,reject)=>{
        options.signal.addEventListener('abort',()=>{aborted=true;reject(options.signal.reason);},{once:true});
    }) : null);
    s.events.focus(); await settle();
    s.setIntercept(null); s.data.owner='account-b'; s.events['neko:account-changed'](); await settle();
    assert.equal(aborted,true);
    assert.equal(s.elements.status.textContent,'feedbackDevices.ready');
    assert.equal(s.announcements.at(-1).detail.owner_key,'account-b');
});

test('an account refresh retains an in-flight mutation owner until it settles', async () => {
    const s = await fixture(); let finishMutation, mutationSignal;
    s.setIntercept((url, options) => url.endsWith('/activate') ? new Promise(resolve => {finishMutation=resolve;mutationSignal=options.signal;}) : null);
    s.elements['replacement-id'].value='old-0'; s.elements['replacement-id'].listeners.change();
    s.elements.activate.click(); await settle();
    s.data.owner='account-b'; s.events['neko:account-changed'](); await settle();
    assert.equal(mutationSignal.aborted, false, 'only read transport is cancelled');
    assert.equal(s.elements.activate.disabled, true);
    assert.ok(s.elements.devices.children.every(row => row.children[1].disabled));
    s.elements.activate.listeners.click(); await settle();
    assert.equal(s.data.calls.filter(row=>row.url.endsWith('/activate')).length, 1);
    finishMutation({ok:false,status:409,json:async()=>({error_code:'cloud_conflict'})}); await settle();
    assert.equal(s.elements.refresh.disabled, false);
    assert.equal(s.elements.status.textContent, 'feedbackDevices.ready', 'old account failure does not overwrite the new account');
    assert.equal(s.announcements.at(-1).detail.owner_key, 'account-b');
});

test('BFCache restore cannot unlock a still-pending remote activation', async () => {
    const s = await fixture(); let finishMutation;
    s.setIntercept(url => url.endsWith('/activate') ? new Promise(resolve => {finishMutation=resolve;}) : null);
    s.elements['replacement-id'].value='old-0'; s.elements['replacement-id'].listeners.change(); s.elements.activate.click(); await settle();
    s.events.pagehide(); s.events.pageshow(); await settle();
    assert.equal(s.elements.refresh.disabled, true);
    s.elements.activate.listeners.click(); await settle();
    assert.equal(s.data.calls.filter(row=>row.url.endsWith('/activate')).length, 1);
    finishMutation({ok:true,json:async()=>({success:true})}); await settle();
    assert.equal(s.elements.refresh.disabled, false);
});

test('account changes during CSRF recovery never resubmit an old mutation', async () => {
    const s = await fixture(); let finishConfig, attempts=0;
    s.setIntercept(url => {
        if(url.endsWith('/activate')) { attempts++; return {ok:false,status:403,json:async()=>({error_code:'csrf_validation_failed'})}; }
        if(url.endsWith('page_config')) return new Promise(resolve=>{finishConfig=resolve;});
        return null;
    });
    s.elements['replacement-id'].value='old-0'; s.elements['replacement-id'].listeners.change(); s.elements.activate.click(); await settle();
    s.setIntercept(null); s.data.owner='account-b'; s.events['neko:account-changed'](); await settle();
    finishConfig({ok:true,json:async()=>({autostart_csrf_token:'late'})}); await settle();
    assert.equal(attempts, 1);
    assert.equal(s.elements.refresh.disabled, false);
    assert.equal(s.announcements.at(-1).detail.owner_key, 'account-b');
});

test('signed-out v2 presents login without requesting devices or activating automatically', async () => {
    const s = await fixture();
    const before=s.data.calls.length;
    s.data.status={signed_in:false,identity_verified:false}; s.events['neko:account-changed'](); await settle();
    assert.equal(s.elements.status.textContent, 'feedbackDevices.signedOut');
    assert.equal(s.elements['device-controls'].hidden, true);
    assert.equal(s.data.calls.slice(before).filter(row=>row.url.endsWith('/devices')||row.options.method==='POST').length, 0);
    assert.equal(s.announcements.at(-1).detail.signed_in, false);
    s.data.protocol=1; s.events.focus(); await settle();
    assert.equal(s.elements.status.textContent, 'feedbackDevices.notEnabled');
    assert.equal(s.announcements.at(-1).detail.signed_in, false, 'v1 still supplies account state to the login card');
});

test('upload diagnostics use backend identity, configuration and receipt fields without changing capability', async () => {
    const s = await fixture();
    for(const [state,key] of [
        [{upload_paused:true,fully_synced:true},'paused'],
        [{control_state:'device_activation_required',identity_verified:false},'awaitingEnable'],
        [{control_state:'registration_removed'},'awaitingEnable'],
        [{last_sync_error:'offline'},'uploadError'],
        [{identity_verified:false},'identityPending'],
        [{fully_synced:true,has_cloud_receipt:true},'fullySynced'],
        [{pending:1},'uploadPending'],[{settings_journal_pending:1},'uploadPending'],[{held:1},'uploadPending'],
        [{fully_synced:false,has_cloud_receipt:false,pending:0},'ready'],
    ]) {
        s.data.status={signed_in:true,...state}; s.events.focus(); await settle();
        assert.equal(s.elements.status.textContent,'feedbackDevices.'+key);
        assert.equal(s.elements['device-controls'].hidden,false,'status does not pretend the runtime setting disables device capability');
    }
    assert.equal(s.data.calls.filter(row=>row.options.method==='POST').length,0);
});

test('native account notifications refresh immediately and subscription follows page lifecycle', async () => {
    let notify, subscriptions=0, removals=0;
    const s=await fixture({electronShell:{onAccountChanged(callback){notify=callback;subscriptions++;return()=>{removals++;};}}});
    s.data.owner='account-b'; notify();
    assert.equal(s.elements.devices.children.length,0); await settle();
    assert.equal(s.announcements.at(-1).detail.owner_key,'account-b');
    s.events.pagehide(); assert.equal(removals,1);
    const before=s.data.calls.length; notify(); await settle(); assert.equal(s.data.calls.length,before);
    s.events.pageshow(); await settle(); assert.equal(subscriptions,2);
});

test('settings hide the feedback entry and the internal page has no duplicate account card or old disclosure entry', () => {
    const api = fs.readFileSync(path.join(root, 'templates/api_key_settings.html'), 'utf8');
    assert.doesNotMatch(api, /href="\/api\/proactive\/feedback\/manage"/);
    const template = fs.readFileSync(path.join(root, 'templates/feedback_devices.html'), 'utf8');
    for (const id of ['account-panel','account-status','account-action','account-message']) assert.ok(!template.includes('id="' + id + '"'));
    assert.ok(!template.includes('feedback-account.js'));
    assert.ok(template.includes('feedback-devices.js?'));
    assert.doesNotMatch(template, /feedback-disclosure|disclosure-open/);
});

test('management copy has the same complete keys in all eight locales', () => {
    let expected;
    for (const locale of ['en','ja','ko','zh-CN','zh-TW','ru','pt','es']) {
        const entries = JSON.parse(fs.readFileSync(path.join(root, 'static/locales', locale + '.json'), 'utf8')).feedbackDevices;
        assert.ok(entries && Object.values(entries).every(value => typeof value === 'string' && value.length));
        assert.equal(typeof entries.notEnabled, 'string');
        for (const key of ['loading','signedOut','identityPending','paused','uploadPending','uploadError','fullySynced','awaitingEnable']) {
            assert.equal(typeof entries[key], 'string', locale + ': ' + key);
        }
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

test('unopened state shows Yui notice and hides management actions, then recovers on pageshow', async () => {
    const s = await fixture();
    assert.equal(s.elements['coming-soon'].hidden, true);
    assert.equal(s.elements['management-content'].hidden, false);
    s.data.protocol = 1;
    s.elements.refresh.click(); await settle();
    assert.equal(s.elements['coming-soon'].hidden, false);
    assert.equal(s.elements['management-content'].hidden, true);
    s.events.localechange();
    assert.equal(s.elements['coming-soon'].hidden, false);
    s.events.pagehide();
    s.data.protocol = 2;
    s.events.pageshow(); await settle();
    assert.equal(s.elements['coming-soon'].hidden, true);
    assert.equal(s.elements['management-content'].hidden, false);
    assert.equal(s.elements.devices.children.length, 5);
});

test('upgrade and read errors remain actionable after an unopened notice', async () => {
    const s = await fixture();
    s.data.protocol = 1;
    s.elements.refresh.click(); await settle();
    s.data.upgrade = true;
    s.events.pagehide(); s.events.pageshow(); await settle();
    assert.equal(s.elements['coming-soon'].hidden, true);
    assert.equal(s.elements['management-content'].hidden, false);
    assert.equal(s.elements.upgrade.hidden, false);
    assert.equal(s.elements.refresh.disabled, false);
    s.setIntercept(() => { throw Error('offline'); });
    s.elements.refresh.click(); await settle();
    assert.equal(s.elements['coming-soon'].hidden, true);
    assert.equal(s.elements['management-content'].hidden, false);
    assert.equal(s.elements.status.textContent, 'feedbackDevices.unavailable');
    assert.equal(s.elements.refresh.disabled, false);
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
