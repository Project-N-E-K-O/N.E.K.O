'use strict';
const assert = require('node:assert/strict');
const { createVoiceManagerServer } = require('./remote_voice_manager_server.cjs');
const { closeTestServer } = require('./remote_voice_page_diagnostics.cjs');
const ref = 'voice_' + 'a'.repeat(32);
function createRecoveryServer() {
    const { server, state } = createVoiceManagerServer();
    const handler = server.listeners('request')[0];
    server.removeAllListeners('request');
    const fixture = { phase: undefined, revision: 7, operation: 'prepared-owner', status: 'unknown', recoveries: [], pending: null };
    state.voices[ref] = { local_ref: ref, provider: 'cosyvoice', origin: 'import', source: 'clone', remote_voice_id: 'ExistingVoice123', availability: 'available', overwrite_status: 'unknown', can_overwrite: true };
    const json = (response, data, status = 200) => { response.writeHead(status, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(data)); };
    const snapshot = () => ({ local_ref: ref, operation_id: fixture.operation, record_revision: fixture.revision,
        overwrite_status: fixture.status, submission_phase: fixture.phase,
        actions: fixture.status === 'failed' ? ['refresh', 'overwrite'] : fixture.phase === 'prepared' ? ['refresh', 'recover'] : ['refresh'] });
    server.on('request', async (request, response) => {
        const url = new URL(request.url, 'http://127.0.0.1');
        if (url.pathname.endsWith('/overwrite_status')) return json(response, { success: true, status: fixture.status, details: { voice_state: snapshot() } });
        if (url.pathname.endsWith('/recover_overwrite')) {
            let body = ''; for await (const bytes of request) body += bytes;
            fixture.recoveries.push(JSON.parse(body));
            if (fixture.onRecovery) fixture.onRecovery();
            if (fixture.beforeRecovery) await fixture.beforeRecovery();
            const submitted = JSON.parse(body);
            if (fixture.phase !== 'prepared' || submitted.operation_id !== fixture.operation || submitted.record_revision !== fixture.revision) return json(response, { success: false, code: 'VOICE_STATE_CHANGED', details: { voice_state: snapshot() } }, 409);
            fixture.status = 'failed'; fixture.revision++;
            return json(response, { success: true, recovered: true, status: 'failed', details: { voice_state: snapshot() } });
        }
        return handler(request, response);
    });
    return { server, state, fixture, ref, close: () => closeTestServer(server) };
}
async function verifyRecoveryPage({ run, waitFor, controlled }) {
    const { fixture, state } = controlled;
    const open = () => run(`RemoteVoiceManager.openStatus(${JSON.stringify(ref)}, ${JSON.stringify(state.voices[ref])}); true`);
    const click = key => run(`Array.from(document.querySelectorAll('.remote-voice-dialog button')).find(button => button.textContent === window.t('voice.remote.${key}')).click(); true`);
    const visible = key => run(`(() => { const button = Array.from(document.querySelectorAll('.remote-voice-dialog button')).find(button => button.textContent === window.t('voice.remote.${key}')); return !!button && !button.hidden; })()`);
    for (const phase of [undefined, 'submission_possible']) {
        fixture.phase = phase; await open();
        await waitFor("document.querySelector('.remote-voice-dialog').getAttribute('aria-busy') === 'false'");
        assert.equal(await visible('recoverPrepared'), false);
        assert.equal(await visible('overwriteAgain'), false);
        assert.equal(fixture.recoveries.length, 0);
    }
    fixture.phase = 'prepared'; await open();
    await waitFor("Array.from(document.querySelectorAll('.remote-voice-dialog button')).some(button => button.textContent === window.t('voice.remote.recoverPrepared') && !button.hidden && !button.disabled)");
    assert.equal(await visible('overwriteAgain'), false);
    await click('recoverPrepared');
    await waitFor("document.querySelector('.remote-voice-status').textContent === window.t('voice.remote.preparedRecovered') && document.querySelector('.remote-voice-dialog').getAttribute('aria-busy') === 'false'");
    assert.deepEqual(fixture.recoveries[0], { context_token: 'controlled-context', operation_id: 'prepared-owner', record_revision: 7 });
    assert.equal(await visible('recoverPrepared'), false);
    assert.equal(await visible('overwriteAgain'), true);
    assert.equal(state.updates.length, 0);
    assert.equal(await run("document.activeElement.textContent === window.t('voice.remote.overwriteAgain')"), true);
    await click('overwriteAgain');
    assert.equal(await run("!!document.querySelector('.remote-voice-dialog input[type=file]')"), true);
    assert.equal(state.updates.length, 0);

    // Closing the dialog retires its HTTP waiter, while server recovery may finish.
    fixture.phase = 'prepared'; fixture.status = 'unknown'; fixture.revision = 9;
    let release;
    fixture.beforeRecovery = () => new Promise(resolve => { release = resolve; });
    const arrival = new Promise(resolve => { fixture.onRecovery = resolve; });
    await open();
    await waitFor("Array.from(document.querySelectorAll('.remote-voice-dialog button')).some(button => button.textContent === window.t('voice.remote.recoverPrepared') && !button.hidden && !button.disabled)");
    await click('recoverPrepared');
    // Wait for the controlled server to reach the explicit recovery barrier.
    let guard;
    try { await Promise.race([arrival, new Promise((_, reject) => { guard = setTimeout(() => reject(Error('Recovery did not reach barrier')), 5000); })]); }
    finally { clearTimeout(guard); delete fixture.onRecovery; }
    await run('RemoteVoiceManager.close(); RemoteVoiceManager.openImport(); true');
    const title = await run("document.querySelector('#remoteVoiceTitle').textContent");
    release(); delete fixture.beforeRecovery;
    await waitFor("document.querySelector('.remote-voice-table tbody tr')");
    assert.equal(await run("document.querySelector('#remoteVoiceTitle').textContent"), title);
    assert.equal(state.updates.length, 0);
    return { preparedOnlyRecovery: true, exactOperationAndRevision: true, explicitOverwriteOnly: true, recoveryFocus: true, lateRecoveryDialogFenced: true };
}
module.exports = { createRecoveryServer, verifyRecoveryPage };
