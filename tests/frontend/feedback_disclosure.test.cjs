const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

function fixture({translated = true} = {}) {
    const listeners = new Map();
    const document = {activeElement: {id: 'body'}};
    class Element {
        constructor(id) { this.id = id; this.dataset = {}; this.events = {}; this.open = false; this.shown = 0; this.textContent = 'initial unknown'; }
        addEventListener(type, callback) { this.events[type] = callback; }
        click() { this.events.click(); }
        focus() { document.activeElement = this; }
        showModal() { this.open = true; this.shown++; }
        close() { this.open = false; this.events.close(); }
    }
    const elements = Object.fromEntries(['feedback-disclosure','disclosure-open','disclosure-close','disclosure-title','disclosure-start','disclosure-stop'].map(id => [id, new Element(id)]));
    document.getElementById = id => elements[id];
    let language = 'en', requests = 0;
    const translate = key => language + ':' + key;
    const window = {
        ...(translated ? {t: translate} : {}),
        addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(callback); },
        removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    };
    for (const key of ['localStorage', 'sessionStorage']) Object.defineProperty(window, key, {get() { throw Error('Explaining uploads must not persist consent'); }});
    const emit = (type, detail) => { for (const callback of [...listeners.get(type) || []]) callback({type, detail}); };
    vm.runInNewContext(fs.readFileSync(path.join(root, 'static/feedback-disclosure.js'), 'utf8'), {
        window, document, fetch() { requests++; throw Error('The explanation must not send requests'); },
    }, {filename: path.join(root, 'static/feedback-disclosure.js')});
    return {elements, document, window, listeners, emit, requests: () => requests,
        setLanguage(value) { language = value; window.t = translate; emit('localechange'); }};
}

test('each document opens an informational dialog without uploading or granting consent', () => {
    const s = fixture(), second = fixture();
    assert.equal(s.elements['feedback-disclosure'].shown, 1);
    assert.equal(second.elements['feedback-disclosure'].shown, 1);
    assert.equal(s.document.activeElement.id, 'disclosure-title');
    assert.equal(s.elements['disclosure-start'].dataset.i18n, 'feedbackDevices.disclosureStartUnknown');
    assert.equal(s.elements['disclosure-stop'].dataset.i18n, 'feedbackDevices.disclosureStopUnknown');
    s.elements['disclosure-open'].click();
    assert.equal(s.elements['feedback-disclosure'].shown, 1, 'an already open dialog is not opened twice');
    s.elements['disclosure-close'].click();
    assert.equal(s.elements['feedback-disclosure'].open, false);
    assert.equal(s.document.activeElement.id, 'disclosure-open');
    s.elements['disclosure-open'].click();
    assert.equal(s.elements['feedback-disclosure'].shown, 2);
    s.elements['feedback-disclosure'].close(); // The same native close event fires after Escape.
    assert.equal(s.document.activeElement.id, 'disclosure-open');
    assert.equal(s.requests(), 0);
});

test('start and stop explanations follow valid capabilities and return to unknown on invalidation', () => {
    const s = fixture();
    for (const [state, start, stop] of [
        [{protocol_version: 1}, 'Automatic', 'Account'],
        [{protocol_version: 2, signed_in: false}, 'Device', 'Device'],
        [{protocol_version: 2, control_state: 'device_activation_required'}, 'Device', 'Device'],
        [{protocol_version: 2, upload_paused: true}, 'Device', 'Device'],
        [null, 'Unknown', 'Unknown'],
        [{protocol_version: 2, upgrade_required: true}, 'Unknown', 'Unknown'],
        [{protocol_version: 3}, 'Unknown', 'Unknown'],
    ]) {
        s.emit('neko:feedback-status', state);
        assert.equal(s.elements['disclosure-start'].textContent, 'en:feedbackDevices.disclosureStart' + start);
        assert.equal(s.elements['disclosure-stop'].textContent, 'en:feedbackDevices.disclosureStop' + stop);
    }
    assert.equal(s.elements['feedback-disclosure'].shown, 1, 'status updates do not reopen the explanation');
    assert.equal(s.requests(), 0);
});

test('language bootstrap and later language changes translate the current capability, not stale identity', () => {
    const s = fixture({translated: false});
    s.emit('neko:feedback-status', {protocol_version: 2, owner_key: 'private-account', access_token: 'private-token'});
    assert.equal(s.elements['disclosure-start'].dataset.i18n, 'feedbackDevices.disclosureStartDevice');
    assert.equal(s.elements['disclosure-start'].textContent, 'initial unknown', 'existing fallback text stays until translation is ready');
    s.setLanguage('ja');
    assert.equal(s.elements['disclosure-start'].textContent, 'ja:feedbackDevices.disclosureStartDevice');
    s.emit('neko:feedback-status', null);
    s.setLanguage('ko');
    assert.equal(s.elements['disclosure-start'].textContent, 'ko:feedbackDevices.disclosureStartUnknown');
    assert.ok(Object.values(s.elements).every(node => !/private-account|private-token/.test(node.textContent)));
});

test('pagehide unsubscribes and closes without stealing focus; pageshow restores one subscription without reopening', () => {
    const s = fixture();
    s.emit('neko:feedback-status', {protocol_version: 2});
    s.emit('pagehide');
    assert.equal(s.listeners.get('neko:feedback-status').size, 0);
    assert.equal(s.listeners.get('localechange').size, 0);
    assert.equal(s.elements['feedback-disclosure'].open, false);
    assert.equal(s.document.activeElement.id, 'disclosure-title', 'teardown does not focus a departing page');
    s.emit('neko:feedback-status', {protocol_version: 1});
    s.emit('pageshow'); s.emit('pageshow');
    assert.equal(s.listeners.get('neko:feedback-status').size, 1);
    assert.equal(s.listeners.get('localechange').size, 1);
    assert.equal(s.elements['disclosure-start'].dataset.i18n, 'feedbackDevices.disclosureStartUnknown');
    assert.equal(s.elements['feedback-disclosure'].shown, 1);
    s.elements['disclosure-open'].click();
    s.emit('neko:feedback-status', {protocol_version: 1});
    assert.equal(s.elements['disclosure-stop'].dataset.i18n, 'feedbackDevices.disclosureStopAccount');
    s.elements['disclosure-close'].click();
    s.emit('pagehide');
    assert.equal(s.listeners.get('neko:feedback-status').size, 0);
});

test('the accessible explanation is outside capability gates and loads before its status producer', () => {
    const template = fs.readFileSync(path.join(root, 'templates/feedback_devices.html'), 'utf8');
    assert.match(template, /<dialog\b[^>]*id="feedback-disclosure"[^>]*aria-labelledby="disclosure-title"/);
    assert.match(template, /<h2\b[^>]*id="disclosure-title"[^>]*tabindex="-1"/);
    assert.match(template, /<button\b[^>]*id="disclosure-close"[^>]*type="button"/);
    assert.ok(template.indexOf('feedback-disclosure.js?') < template.indexOf('feedback-devices.js?'));
    assert.ok(template.indexOf('id="disclosure-open"') < template.indexOf('id="coming-soon"'));
    for (const locale of ['en','ja','ko','zh-CN','zh-TW','ru','pt','es']) {
        const copy = JSON.parse(fs.readFileSync(path.join(root, 'static/locales', locale + '.json'), 'utf8')).feedbackDevices;
        for (const key of ['entryTitle','participatingDevices','disclosureOpen','disclosureTitle','disclosureIntro','disclosureDataTitle','disclosureData','disclosureExcluded','disclosureStartTitle','disclosureStartUnknown','disclosureStartAutomatic','disclosureStartDevice','disclosureStopTitle','disclosureStopUnknown','disclosureStopAccount','disclosureStopDevice','disclosureRetention','disclosureClose']) {
            assert.ok(copy[key]?.trim(), locale + ': ' + key);
        }
    }
});
