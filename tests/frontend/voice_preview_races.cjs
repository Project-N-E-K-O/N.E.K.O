'use strict';
const assert = require('node:assert/strict');
const { bounded } = require('./voice_preview_server.cjs');

async function verifyPreviewBodyRaces({ run, transport, page = false, diagnostics }) {
    await run(`(() => {
        window.__previewAudios = []; window.__previewNotices = []; window.__previewDeadlines = [];
        window.__previewReadCount = 0; window.__previewReadWaiters = [];
        window.__previewWaitRead = count => window.__previewReadCount >= count ? Promise.resolve(true) : new Promise(resolve => window.__previewReadWaiters.push({ count, resolve }));
        const realFetch = fetch;
        window.fetch = async (...args) => {
            const response = await realFetch(...args);
            if (String(args[0]).includes('/voice_preview?')) {
                for (const method of ['text', 'json']) {
                    const read = response[method].bind(response);
                    response[method] = (...readArgs) => {
                        window.__previewReadCount++;
                        window.__previewReadWaiters.forEach(waiter => { if (window.__previewReadCount >= waiter.count) waiter.resolve(true); });
                        return read(...readArgs);
                    };
                }
            }
            return response;
        };
        window.__previewRealSetTimeout = setTimeout;
        window.setTimeout = (callback, ms, ...args) => {
            if (ms === 30000 || ms === 5000) {
                const timer = { callback, active: true };
                window.__previewDeadlines.push(timer); return timer;
            }
            return window.__previewRealSetTimeout(callback, ms, ...args);
        };
        const realClearTimeout = clearTimeout;
        window.clearTimeout = timer => { if (timer && typeof timer === 'object' && 'active' in timer) timer.active = false; else realClearTimeout(timer); };
        window.showVoicePreviewErrorNotice = value => window.__previewNotices.push(value);
        window.Audio = class {
            constructor(src) { this.src = src; this.events = {}; window.__previewAudios.push(this); }
            addEventListener(name, callback) { this.events[name] = callback; }
            play() { this.played = true; return Promise.resolve(); }
            pause() { this.paused = true; }
            removeAttribute() { this.src = ''; }
            load() { this.released = true; }
        };
        localStorage.clear();
        window.__previewStart = (revision = '') => {
            const btn = document.createElement('button'); document.body.appendChild(btn);
            window.__previewPending = playPreview('preview-body', btn, { source:'clone', origin:'import', remote_revision:revision });
        };
        return true;
    })()`);
    const snapshot = () => run(`({ audio: window.__previewAudios.length, notices: window.__previewNotices.length, cache: localStorage.getItem('voice_preview_preview-body'), sessions: activeVoicePreviewSessions.size })`);
    const cancel = () => run(`(() => { finishVoicePreviewSession(activeVoicePreviewSessions.get('preview-body')); return true; })()`);
    // Cancel after response headers and partial JSON. Releasing the body is deliberately unnecessary.
    await run('window.__previewStart(); true');
    await transport.request(0);
    await bounded(run('window.__previewWaitRead(1)'), 'cancel body read starts');
    await cancel();
    await transport.assertClosed(0);
    await bounded(run('window.__previewPending.then(() => true)'), 'cancel settles');
    assert.deepEqual(await snapshot(), { audio: 0, notices: 0, cache: null, sessions: 0 });
    assert.equal(transport.requests.length, 1);

    // A new revision replaces the old session while its body is being downloaded.
    await run('window.__previewStart("old"); true');
    await transport.request(1);
    await bounded(run('window.__previewWaitRead(2)'), 'replacement body read starts');
    await run('window.__previewOldPending = window.__previewPending; window.__previewStart("new"); true');
    await transport.assertClosed(1);
    await transport.request(2);
    await bounded(run('window.__previewWaitRead(3)'), 'new body read starts');
    await bounded(run('window.__previewOldPending.then(() => true)'), 'replaced settles');
    assert.equal(await run("activeVoicePreviewSessions.get('preview-body').controller !== null"), true);
    transport.requests[2].release();
    await bounded(run('window.__previewPending.then(() => true)'), 'replacement completes');
    assert.equal((await snapshot()).audio, 1);
    assert.match((await snapshot()).cache, /new/);
    await run('window.__previewAudios[0].events.ended(); true');
    assert.deepEqual(await run('({ paused:window.__previewAudios[0].paused, released:window.__previewAudios[0].released, sessions:activeVoicePreviewSessions.size })'), { paused: true, released: true, sessions: 0 });

    // Fire the existing production deadline explicitly after partial delivery.
    await run('localStorage.clear(); window.__previewStart("timeout"); true');
    await transport.request(3);
    await bounded(run('window.__previewWaitRead(4)'), 'timeout body read starts');
    await run('window.__previewDeadlines.findLast(timer => timer.active).callback(); true');
    await transport.assertClosed(3);
    await transport.request(4);
    await bounded(run('window.__previewWaitRead(5)'), 'retry body read starts');
    transport.requests[4].release();
    await bounded(run('window.__previewPending.then(() => true)'), 'timeout retry completes');
    assert.equal((await snapshot()).audio, 2);
    assert.equal((await snapshot()).notices, 0);
    await cancel();

    // Both permitted clone attempts time out during body reads: do not accept
    // either partial result or silently extend the existing retry count.
    await run('localStorage.clear(); window.__previewStart("exhausted"); true');
    const assertTimeoutLog = diagnostics?.expectConsoleError(args => args.length === 2 &&
        args[0].value === 'Preview error:' && args[1].className === 'DOMException' &&
        args[1].description?.startsWith('AbortError:'));
    for (let index = 5; index <= 6; index++) {
        await transport.request(index);
        await bounded(run('window.__previewWaitRead(' + (index + 1) + ')'), 'exhausted body read starts');
        await run('window.__previewDeadlines.findLast(timer => timer.active).callback(); true');
        await transport.assertClosed(index);
    }
    await bounded(run('window.__previewPending.then(() => true)'), 'exhausted timeout settles');
    if (assertTimeoutLog) assertTimeoutLog();
    assert.deepEqual(await snapshot(), { audio: 2, notices: 1, cache: null, sessions: 0 });
    assert.equal(transport.requests.length, 7);

    if (page) {
        // Exercise the product deletion path, including its cache invalidation and list refresh.
        transport.state.voices['preview-body'] = { origin: 'import', source: 'clone', provider: 'cosyvoice', remote_revision:'delete', availability:'available' };
        await run('localStorage.clear(); window.confirm = () => true; window.__previewStart("delete"); true');
        await transport.request(7);
        await bounded(run('window.__previewWaitRead(8)'), 'delete body read starts');
        await run('deleteVoice("preview-body", "Controlled preview")');
        await transport.assertClosed(7);
        await bounded(run('window.__previewPending.then(() => true)'), 'delete settles');
        assert.equal((await snapshot()).sessions, 0);
        assert.equal((await snapshot()).audio, 2);
        assert.equal((await snapshot()).notices, 1);
        assert.equal((await snapshot()).cache, null);
    }
    return { bodyCancellationClosesTransport: true, replacementFenced: true, bodyTimeoutRetried: true, bodyTimeoutExhaustionBounded: true,
        audioEndReleased: true, ...(page ? { deletionCancelsBody: true } : {}) };
}
module.exports = { verifyPreviewBodyRaces };
