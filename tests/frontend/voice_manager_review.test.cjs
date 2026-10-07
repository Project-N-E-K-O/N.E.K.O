'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/js/remote_voice_manager.js'), 'utf8');
const existingTests = fs.readFileSync(path.join(__dirname, 'remote_voice_manager.test.cjs'), 'utf8');
const start = existingTests.indexOf('class Element {');
const end = existingTests.indexOf('function searchClock()');
assert.ok(start >= 0 && end > start, 'The shared manager DOM harness must remain available');
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};
// Reuse the existing full-script DOM harness without registering its test cases.
const harness = vm.runInNewContext(existingTests.slice(start, end) + '\nharness;', {
    source, path, vm, __dirname, deferred, setTimeout, clearTimeout,
    AbortController, DOMException, URLSearchParams, FormData,
});
const tick = () => new Promise(resolve => setImmediate(resolve));
const context = (h, token = 'old-context') => ({ ...h.ctx, provider: 'cosyvoice', context_token: token,
    capabilities: { ...h.ctx.capabilities, overwrite: true } });
const state = (status = 'unknown', revision = 7) => ({ success: true, status, details: {
    voice_state: { local_ref: 'voice-local', operation_id: 'existing-operation',
        record_revision: revision, overwrite_status: status,
        actions: status === 'failed' ? ['refresh', 'overwrite'] : ['refresh'] },
} });
async function cleanup(h) {
    h.window.RemoteVoiceManager.close();
    for (let index = 0; index < h.requests.length; index++) h.resolve(index, { success: false, code: 'CANCELLED' }, 409);
    await tick();
}
async function openPendingStatus(h) {
    h.window.RemoteVoiceManager.openStatus('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    h.resolve(0, context(h)); await tick();
    h.resolve(1, state()); await tick();
}
async function rejectChangedQuery(h) {
    h.button('refreshStatus').dispatch('click');
    // Permit either a fresh-context strategy or cache invalidation on conflict.
    if (h.requests.at(-1).url.includes('/context?')) {
        h.resolve(h.requests.length - 1, context(h)); await tick();
    }
    assert.ok(h.requests.at(-1).url.includes('/overwrite_status?'));
    h.resolve(h.requests.length - 1, { success: false, code: 'CONTEXT_CHANGED',
        details: { attempt_outcome: 'not_submitted', state_sync: 'unchanged', voice_state: null } }, 409);
    await tick();
}

for (const scenario of ['deleted', 'old-pending', 'transport']) {
    test('overwrite ' + scenario + ' separates the attempt reason from submission controls', async () => {
        const h = harness();
        try {
            h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
            const audio = h.panel().querySelectorAll('input')[0];
            audio.files = [new Blob(['isolated audio'])]; audio.dispatch('change');
            h.button('overwrite').dispatch('click'); h.resolve(0, context(h)); await tick();
            if (scenario === 'transport') h.requests[1].reject(new TypeError('controlled response loss'));
            else h.resolve(1, { success: false,
                code: scenario === 'deleted' ? 'VOICE_NOT_FOUND' : 'UPDATE_OUTCOME_UNKNOWN',
                details: { attempt_outcome: 'not_submitted', state_sync: 'unchanged',
                    voice_state: scenario === 'deleted' ? null : state().details.voice_state }
            }, scenario === 'deleted' ? 404 : 409);
            await tick();
            assert.equal(h.panel().querySelector('.remote-voice-status').textContent,
                'voice.remote.' + (scenario === 'deleted' ? 'voiceNotFound' : 'uncertain'));
            assert.equal(h.button('overwrite').hidden, true);
            assert.equal(h.button('refreshStatus').hidden, false);
            assert.equal(h.button('refreshStatus').disabled, false);
            assert.equal(h.panel().attributes['aria-busy'], 'false');
            assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 1);
        } finally { await cleanup(h); }
    });
}

for (const status of ['failed', 'completed']) {
    test('legacy ' + status + ' overwrite result retains a query exit without enabling resubmission', async () => {
        const h = harness();
        try {
            h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
            const audio = h.panel().querySelectorAll('input')[0];
            audio.files = [new Blob(['isolated audio'])]; audio.dispatch('change');
            h.button('overwrite').dispatch('click'); h.resolve(0, context(h)); await tick();
            h.resolve(1, { success: true, status }); await tick();
            assert.equal(h.button('overwrite').hidden, true);
            assert.equal(h.button('refreshStatus').hidden, false);
            assert.equal(h.button('refreshStatus').disabled, false);
            assert.equal(h.panel().attributes['aria-busy'], 'false');
            assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 1);
        } finally { await cleanup(h); }
    });
}

test('legacy terminal query response keeps query available inside the same status dialog', async () => {
    const h = harness();
    try {
        await openPendingStatus(h);
        h.button('refreshStatus').dispatch('click');
        if (h.requests.at(-1).url.includes('/context?')) {
            h.resolve(h.requests.length - 1, context(h)); await tick();
        }
        h.resolve(h.requests.length - 1, { success: true, status: 'failed' }); await tick();
        assert.equal(h.button('refreshStatus').hidden, false);
        assert.equal(h.button('refreshStatus').disabled, false);
        assert.equal(h.button('overwriteAgain').hidden, true);
        assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 0);
    } finally { await cleanup(h); }
});

test('status context conflict reports its cause and releases controls', async () => {
    const h = harness();
    try {
        await openPendingStatus(h); await rejectChangedQuery(h);
        assert.equal(h.panel().querySelector('.remote-voice-status').textContent, 'voice.remote.contextChanged');
        assert.equal(h.button('refreshStatus').hidden, false);
        assert.equal(h.button('refreshStatus').disabled, false);
        assert.equal(h.button('overwriteAgain').hidden, true);
        assert.equal(h.panel().attributes['aria-busy'], 'false');
    } finally { await cleanup(h); }
});

test('query retry after context conflict refreshes context and never submits an overwrite', async () => {
    const h = harness();
    try {
        await openPendingStatus(h); await rejectChangedQuery(h);
        h.button('refreshStatus').dispatch('click');
        assert.ok(h.requests.at(-1).url.includes('/context?'), 'Retry must obtain a current context instead of replaying the rejected token');
        h.resolve(h.requests.length - 1, context(h, 'new-context')); await tick();
        assert.equal(new URL(h.requests.at(-1).url, 'http://isolated.test').searchParams.get('context_token'), 'new-context');
        h.resolve(h.requests.length - 1, state('failed', 8)); await tick();
        assert.equal(h.button('overwriteAgain').hidden, false);
        assert.equal(h.button('overwriteAgain').disabled, false);
        assert.equal(h.panel().attributes['aria-busy'], 'false');
        assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 0);
    } finally { await cleanup(h); }
});

async function openRecoverableStatus(h) {
    h.window.RemoteVoiceManager.openStatus('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    h.resolve(0, context(h)); await tick();
    const current = state(); current.details.voice_state.actions.push('recover');
    h.resolve(1, current); await tick();
}

for (const failure of ['CONTEXT_CHANGED', 'transport']) {
    test('recovery context ' + failure + ' before submission reports its cause without claiming an uncertain recovery', async () => {
        const h = harness();
        try {
            await openRecoverableStatus(h);
            h.button('recoverPrepared').dispatch('click');
            assert.ok(h.requests.at(-1).url.includes('/context?'));
            if (failure === 'transport') h.requests.at(-1).reject(new TypeError('controlled connection failure'));
            else h.resolve(h.requests.length - 1, { success: false, code: failure }, 409);
            await tick();
            assert.equal(h.panel().querySelector('.remote-voice-status').textContent,
                'voice.remote.' + (failure === 'transport' ? 'requestFailed' : 'contextChanged'));
            assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 0);
            assert.equal(h.button('recoverPrepared').hidden, true);
            assert.equal(h.button('refreshStatus').hidden, false);
            assert.equal(h.panel().attributes['aria-busy'], 'false');
        } finally { await cleanup(h); }
    });
}

test('a confirmed recovery permission failure retains the permission error with a null snapshot', async () => {
    const h = harness();
    try {
        await openRecoverableStatus(h);
        h.button('recoverPrepared').dispatch('click');
        h.resolve(h.requests.length - 1, context(h)); await tick();
        assert.ok(h.requests.at(-1).url.endsWith('/recover_overwrite'));
        h.resolve(h.requests.length - 1, { success: false, code: 'PERMISSION_DENIED',
            details: { attempt_outcome: 'not_submitted', state_sync: 'unchanged', voice_state: null } }, 403);
        await tick();
        assert.equal(h.panel().querySelector('.remote-voice-status').textContent, 'voice.remote.permissionDenied');
        assert.equal(h.button('recoverPrepared').hidden, true);
        assert.equal(h.button('refreshStatus').hidden, false);
        assert.equal(h.panel().attributes['aria-busy'], 'false');
    } finally { await cleanup(h); }
});

test('an interrupted recovery response after submission remains uncertain and only offers query', async () => {
    const h = harness();
    try {
        await openRecoverableStatus(h);
        h.button('recoverPrepared').dispatch('click');
        h.resolve(h.requests.length - 1, context(h)); await tick();
        h.requests.at(-1).reject(new TypeError('controlled response loss')); await tick();
        assert.equal(h.panel().querySelector('.remote-voice-status').textContent, 'voice.remote.recoveryUncertain');
        assert.equal(h.button('recoverPrepared').hidden, true);
        assert.equal(h.button('refreshStatus').hidden, false);
        assert.equal(h.button('overwriteAgain').hidden, true);
        assert.equal(h.panel().attributes['aria-busy'], 'false');
    } finally { await cleanup(h); }
});

test('query after a recovery context conflict obtains a current context', async () => {
    const h = harness();
    try {
        await openRecoverableStatus(h);
        h.button('recoverPrepared').dispatch('click');
        h.resolve(h.requests.length - 1, { success: false, code: 'CONTEXT_CHANGED' }, 409); await tick();
        h.button('refreshStatus').dispatch('click');
        assert.ok(h.requests.at(-1).url.includes('/context?'), 'Recovery conflict must retire the cached context before querying');
        h.resolve(h.requests.length - 1, context(h, 'new-context')); await tick();
        assert.equal(new URL(h.requests.at(-1).url, 'http://isolated.test').searchParams.get('context_token'), 'new-context');
        h.resolve(h.requests.length - 1, state()); await tick();
        assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 0);
    } finally { await cleanup(h); }
});

test('overwrite context conflict keeps its cause and the next explicit query obtains a current context', async () => {
    const h = harness();
    try {
        h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
        const audio = h.panel().querySelectorAll('input')[0];
        audio.files = [new Blob(['isolated audio'])]; audio.dispatch('change');
        h.button('overwrite').dispatch('click'); h.resolve(0, context(h)); await tick();
        h.resolve(1, { success: false, code: 'CONTEXT_CHANGED',
            details: { attempt_outcome: 'not_submitted', state_sync: 'unchanged', voice_state: null } }, 409);
        await tick();
        assert.equal(h.panel().querySelector('.remote-voice-status').textContent, 'voice.remote.contextChanged');
        assert.equal(h.button('overwrite').hidden, true);
        assert.equal(h.button('refreshStatus').hidden, false);
        assert.equal(h.panel().attributes['aria-busy'], 'false');
        h.button('refreshStatus').dispatch('click');
        assert.ok(h.requests.at(-1).url.includes('/context?'), 'Overwrite conflict must retire the cached context before querying');
        h.resolve(h.requests.length - 1, context(h, 'new-context')); await tick();
        assert.equal(new URL(h.requests.at(-1).url, 'http://isolated.test').searchParams.get('context_token'), 'new-context');
        h.resolve(h.requests.length - 1, state('failed', 8)); await tick();
        assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 1);
    } finally { await cleanup(h); }
});
