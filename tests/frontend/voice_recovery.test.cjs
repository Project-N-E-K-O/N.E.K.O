'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { RemoteVoiceOperation } = require('../../static/js/remote_voice_manager.js');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/remote_voice_manager.js'), 'utf8');
const ref = 'voice_' + 'a'.repeat(32);
const snapshot = (revision = 7, actions = ['refresh', 'recover'], status = 'processing', operation = 'prepared-owner') => ({
    local_ref: ref, operation_id: operation, record_revision: revision, overwrite_status: status, actions
});
const result = state => ({ success: true, details: { voice_state: state } });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { resolve, promise }; };
function harness() {
    const control = () => ({ hidden: true, disabled: false, focus() { this.focused = true; } });
    const state = { localRef: ref, provider: 'cosyvoice', mode: 'overwrite', busy: false,
        context: { capabilities: { overwrite: true } }, panel: { setAttribute() {} },
        status: { textContent: '', classList: { remove() {} } },
        audio: { ...control(), files: [] }, submit: control(), refreshStatus: control(),
        abandonUnknown: control(), recoverPrepared: control(), recoveryHint: control(), reopenOverwrite: control()
    };
    const requests = [], contextGate = deferred();
    const operations = new RemoteVoiceOperation(async (url, options) => {
        const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise;
    });
    const context = vm.createContext({ active: state, operations, Number, Array,
        root: { confirm: () => true, loadVoices: async () => {} },
        busy: (target, value) => { target.busy = value; }, t: key => key,
        showError: (target, error) => { target.status.textContent = error.code || 'requestFailed'; },
        context: async () => contextGate.promise
    });
    vm.runInContext(source.slice(source.indexOf('    function updateOverwriteView('), source.indexOf('    function openOverwrite(')), context);
    return { state, context, requests, contextGate,
        apply: value => context.updateOverwriteView(state, result(value)),
        recover: () => context.recoverPreparedOverwrite(state),
        abandon: () => context.recoverPreparedOverwrite(state, true),
        resolve: (value, status = 200) => requests[0].resolve({ ok: status < 400, status, json: async () => value }) };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
test('only a current server recovery action exposes preparation cancellation', () => {
    const h = harness();
    h.apply(snapshot());
    assert.equal(h.state.recoverPrepared.hidden, false);
    assert.equal(h.state.submit.hidden, true);
    assert.equal(h.state.recoveryHint.hidden, false);
    const expected = h.state.recoverySnapshot;
    h.apply(snapshot(6, ['refresh']));
    assert.equal(h.state.recoverySnapshot, expected);
    for (const state of [snapshot(8, ['refresh']), snapshot(9, ['recover'], 'failed'), { ...snapshot(10), operation_id: '' }]) {
        h.apply(state); assert.equal(h.state.recoverPrepared.hidden, true);
    }
});
test('recovery uses fresh context and exact identity, then exposes explicit overwrite without submitting', async () => {
    const h = harness(); h.apply(snapshot());
    const pending = h.recover();
    assert.equal(h.state.busy, true);
    h.contextGate.resolve({ context_token: 'fresh-context' }); await tick();
    assert.equal(h.requests.length, 1);
    assert.ok(h.requests[0].url.endsWith('/' + ref + '/recover_overwrite'));
    assert.deepEqual(JSON.parse(h.requests[0].options.body), { context_token: 'fresh-context', operation_id: 'prepared-owner', record_revision: 7 });
    h.resolve({ ...result(snapshot(8, ['refresh', 'overwrite'], 'failed')), recovered: true });
    await pending;
    assert.equal(h.state.status.textContent, 'preparedRecovered');
    assert.equal(h.state.recoverPrepared.hidden, true);
    assert.equal(h.state.submit.hidden, false);
    assert.equal(h.requests.length, 1);
    assert.equal(h.state.busy, false);
});
test('changed preparation or dialog during context await prevents recovery submission', async () => {
    for (const replacement of ['snapshot', 'dialog']) {
        const h = harness(); h.apply(snapshot()); const pending = h.recover();
        if (replacement === 'snapshot') h.apply(snapshot(8)); else h.context.active = {};
        h.contextGate.resolve({ context_token: 'fresh-context' }); await pending;
        assert.equal(h.requests.length, 0);
    }
});
test('a successor snapshot and late response cannot claim the old preparation was unlocked', async () => {
    const h = harness(); h.apply(snapshot()); const pending = h.recover();
    h.contextGate.resolve({ context_token: 'fresh-context' }); await tick();
    h.resolve({ ...result(snapshot(9, ['refresh'], 'unknown', 'new-owner')), recovered: true });
    await pending;
    assert.notEqual(h.state.status.textContent, 'preparedRecovered');
    assert.equal(h.state.submit.hidden, true);
    assert.equal(h.state.recoverPrepared.hidden, true);
});
test('a saved recovery without a readable snapshot keeps only a query exit', async () => {
    const h = harness(); h.apply(snapshot()); const pending = h.recover();
    h.contextGate.resolve({ context_token: 'fresh-context' }); await tick();
    h.resolve({ success: true, recovered: true, status: 'failed', details: { voice_state: null, state_sync: 'saved' } });
    await pending;
    assert.equal(h.state.submit.hidden, true);
    assert.equal(h.state.recoverPrepared.hidden, true);
    assert.equal(h.state.refreshStatus.hidden, false);
    assert.notEqual(h.state.status.textContent, 'preparedRecovered');
});
test('save failure retains pending actions, unknown transport result requires a query', async () => {
    for (const details of [{ voice_state: snapshot() }, undefined]) {
        const h = harness(); h.apply(snapshot()); const pending = h.recover();
        h.contextGate.resolve({ context_token: 'fresh-context' }); await tick();
        h.resolve({ success: false, code: 'STORAGE_ERROR', details }, 500); await pending;
        assert.equal(h.state.submit.hidden, true);
        assert.equal(h.state.refreshStatus.hidden, false);
        assert.equal(h.state.recoverPrepared.hidden, !details);
        assert.equal(h.state.status.textContent, details ? 'STORAGE_ERROR' : 'recoveryUncertain');
        assert.equal(h.state.busy, false);
    }
});
test('all locales translate recovery controls and invalidate the language cache', () => {
    for (const locale of ['en', 'ja', 'ko', 'zh-CN', 'zh-TW', 'ru', 'es', 'pt']) {
        const translations = JSON.parse(fs.readFileSync(path.join(__dirname, '../../static/locales', locale + '.json'), 'utf8')).voice.remote;
        for (const key of ['recoverPrepared', 'recoverPreparedHint', 'recoveringPrepared', 'preparedRecovered', 'overwriteAgain', 'recoveryUncertain', 'abandonUnknown', 'abandonUnknownConfirm', 'abandoningUnknown', 'unknownAbandoned']) assert.ok(translations[key], locale + ':' + key);
    }
    const bootstrap = fs.readFileSync(path.join(__dirname, '../../static/i18n-i18next.js'), 'utf8');
    const version = bootstrap.match(/const\s+LOCALE_VERSION\s*=\s*'(\d{4}-\d{2}-\d{2})-[^']+'/);
    assert.ok(version, 'locale cache version must include its release date');
    // Later features also bump this shared version; its slug need not name voice recovery.
    assert.ok(version[1] >= '2026-10-08', 'locale cache must include the recovery controls');
});


test('unknown unlock requires server advice and explicit risk confirmation', async () => {
    const h = harness(); h.apply(snapshot(7, ['refresh'], 'unknown'));
    assert.equal(h.state.abandonUnknown.hidden, true);
    h.apply(snapshot(8, ['refresh', 'abandon'], 'unknown'));
    assert.equal(h.state.abandonUnknown.hidden, false);
    let prompt;
    h.context.root.confirm = message => { prompt = message; return false; };
    await h.abandon();
    assert.equal(prompt, 'abandonUnknownConfirm');
    assert.equal(h.requests.length, 0);
    h.context.root.confirm = () => true;
    const pending = h.abandon();
    h.contextGate.resolve({ context_token: 'fresh-context' }); await tick();
    assert.ok(h.requests[0].url.endsWith('/abandon_overwrite'));
    assert.deepEqual(JSON.parse(h.requests[0].options.body), { context_token: 'fresh-context', operation_id: 'prepared-owner', record_revision: 8 });
    h.resolve({ ...result(snapshot(9, ['refresh', 'overwrite'], 'failed')), abandoned: true });
    await pending;
    assert.equal(h.state.status.textContent, 'unknownAbandoned');
    assert.equal(h.state.submit.hidden, false);
    assert.equal(h.state.abandonUnknown.hidden, true);
    assert.equal(h.requests.length, 1);
});
test('changed unknown owner during context wait prevents unlock', async () => {
    const h = harness(); h.apply(snapshot(7, ['refresh', 'abandon'], 'unknown'));
    const pending = h.abandon(); h.apply(snapshot(8, ['refresh'], 'unknown', 'successor'));
    h.contextGate.resolve({ context_token: 'fresh-context' }); await pending;
    assert.equal(h.requests.length, 0);
});
