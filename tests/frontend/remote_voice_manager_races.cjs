'use strict';
// Shared controlled barriers exercise the same product flow in Chromium and Electron.
const assert = require('node:assert/strict');
function gate() {
    let entered, release;
    const started = new Promise(resolve => { entered = resolve; });
    const pending = new Promise(resolve => { release = resolve; });
    return { started, release, hold: () => { entered(); return pending; } };
}
async function bounded(promise) {
    let timer;
    try {
        await Promise.race([promise, new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error('Controlled API barrier was not reached')), 10000);
        })]);
    } finally { clearTimeout(timer); }
}
async function verifyOverwriteClaimConflict({ run, waitFor, state }) {
    const ref = Object.keys(state.voices).find(key => state.voices[key].provider === 'cosyvoice');
    assert.ok(ref);
    const original = state.voices[ref], binding = state.binding;
    const initialUpdates = state.updates.length, initialQueries = state.statusQueries || 0;
    const submit = "Array.from(document.querySelectorAll('.remote-voice-dialog button')).find(button=>button.textContent===window.t('voice.remote.overwrite'))";
    state.overwriteConflict = true;
    try {
        for (const terminal of ['completed', 'failed']) {
            state.voices[ref] = { ...original, overwrite_status: terminal, can_overwrite: true };
            const winner = JSON.parse(JSON.stringify(state.voices[ref]));
            await run('window.loadVoices()');
            await run(`Array.from(document.querySelectorAll('[data-voice-id="${ref}"] button')).find(button=>button.textContent===window.t('voice.remote.overwrite')).click();true;`);
            await run("(()=>{const files=new DataTransfer();files.items.add(new File([new Uint8Array(512)],'retained.wav',{type:'audio/wav'}));const input=document.querySelector('.remote-voice-dialog input[type=file]');input.files=files.files;input.dispatchEvent(new Event('change',{bubbles:true}));return true;})()");
            for (let attempt = 0; attempt < 2; attempt++) {
                const rejected = (state.rejectedUpdates || []).length;
                await run(submit + '.click();true;');
                await waitFor("document.querySelector('.remote-voice-status').textContent===window.t('voice.remote.voiceStateChanged') && document.querySelector('.remote-voice-dialog').getAttribute('aria-busy')==='false'");
                assert.equal((state.rejectedUpdates || []).length, rejected + 1);
                assert.equal(await run(`(()=>{const button=${submit};return !button.hidden&&!button.disabled;})()`), true);
                assert.equal(await run("document.querySelector('.remote-voice-dialog input[type=file]').files[0].name"), 'retained.wav');
                assert.equal(await run("Array.from(document.querySelectorAll('.remote-voice-dialog button')).find(button=>button.textContent===window.t('voice.remote.refreshStatus')).hidden"), true);
                assert.equal(state.updates.length, initialUpdates);
                assert.equal(state.statusQueries || 0, initialQueries);
                assert.equal(state.binding, binding);
                assert.deepEqual(state.voices[ref], winner);
            }
            await run("document.querySelector('.remote-voice-close').click();true;");
        }
    } finally {
        delete state.overwriteConflict;
        state.voices[ref] = original;
    }
}

async function verifyOverwriteOutcomes({ run, waitFor, state }) {
    const ref = Object.keys(state.voices).find(key => state.voices[key].provider === 'cosyvoice');
    const original = state.voices[ref], binding = state.binding;
    const control = key => `Array.from(document.querySelectorAll('.remote-voice-dialog button')).find(button=>button.textContent===window.t('voice.remote.${key}'))`;
    const open = async () => {
        await run(`window.RemoteVoiceManager.openOverwrite(${JSON.stringify(ref)}, ${JSON.stringify(original)});true;`);
        await run("(()=>{const files=new DataTransfer();files.items.add(new File([new Uint8Array(512)],'retained.wav',{type:'audio/wav'}));const input=document.querySelector('.remote-voice-dialog input[type=file]');input.files=files.files;input.dispatchEvent(new Event('change',{bubbles:true}));return true;})()");
    };
    try {
        for (const mode of ['stale-pending', 'active', 'rejected', 'save-failure']) {
            state.voices[ref] = { ...original, overwrite_status: 'completed', can_overwrite: true };
            state.overwriteMode = mode;
            const beforeUpdates = state.updates.length;
            const beforeAttempts = (state.overwriteAttempts || []).length;
            await open();
            await run(control('overwrite') + '.click();true;');
            await waitFor("document.querySelector('.remote-voice-dialog').getAttribute('aria-busy')==='false'");
            assert.equal((state.overwriteAttempts || []).length, beforeAttempts + 1);
            assert.equal(state.updates.length, beforeUpdates + (mode === 'rejected' || mode === 'save-failure' ? 1 : 0));
            assert.equal(await run(control('overwrite') + '.hidden'), mode !== 'rejected');
            assert.equal(await run(control('refreshStatus') + '.hidden'), false);
            assert.equal(await run(control('refreshStatus') + '.disabled'), false);
            assert.equal(await run("document.querySelector('.remote-voice-dialog input[type=file]').files[0].name"), 'retained.wav');
            state.statusResult = 'failed';
            await run(control('refreshStatus') + '.click();true;');
            await waitFor("document.querySelector('.remote-voice-status').textContent===window.t('voice.remote.failed') && document.querySelector('.remote-voice-dialog').getAttribute('aria-busy')==='false'");
            assert.equal(await run(control('overwrite') + '.hidden'), false);
            assert.equal(await run(control('overwrite') + '.disabled'), false);
            assert.equal((state.overwriteAttempts || []).length, beforeAttempts + 1);
            assert.equal(state.binding, binding);
            await run('window.RemoteVoiceManager.close();true;');
        }
        // A delayed error belongs to the closed dialog, including its actions.
        const responseGate = gate();
        state.beforeOverwriteResponse = responseGate.hold;
        state.overwriteMode = 'stale-pending';
        await run(`(() => {
            window.__lateOriginalFetch = window.fetch;
            let consumed;
            window.__lateReadDone = new Promise(resolve => { consumed = resolve; });
            window.fetch = async (url, options) => {
                if (!String(url).endsWith('/overwrite')) return window.__lateOriginalFetch(url, options);
                // Deliberately ignore client abort so a real late body must be
                // rejected by the dialog identity fence too.
                const response = await window.__lateOriginalFetch(url, { ...options, signal: undefined });
                const read = response.json.bind(response);
                response.json = async () => { try { return await read(); } finally { consumed(); } };
                return response;
            };
            return true;
        })()`);
        await open();
        await run(control('overwrite') + '.click();true;');
        try {
            await bounded(responseGate.started);
            await run('window.RemoteVoiceManager.close();true;');
            delete state.beforeOverwriteResponse;
            await open();
            const statusBefore = await run("document.querySelector('.remote-voice-status').textContent");
            responseGate.release();
            await run('window.__lateReadDone.then(()=>new Promise(resolve=>setTimeout(resolve,0)))');
            assert.equal(await run("document.querySelector('.remote-voice-status').textContent"), statusBefore);
            assert.equal(await run(control('overwrite') + '.hidden'), false);
            assert.equal(await run(control('refreshStatus') + '.hidden'), true);
        } finally {
            responseGate.release();
            await run('window.fetch=window.__lateOriginalFetch;true;');
        }
        await run('window.RemoteVoiceManager.close();true;');
        // The visible form follows the server-owned resource descriptor.
        await run("document.getElementById('voiceProvider').value='doubao_tts';document.getElementById('voiceProvider').dispatchEvent(new Event('change'));window.RemoteVoiceManager.openImport();true;");
        await waitFor("document.querySelector('.remote-voice-dialog').getAttribute('aria-busy')==='false'");
        await run(control('manualEntry') + '.click();true;');
        assert.deepEqual(await run("(()=>{const field=document.querySelector('input[name=doubao_resource_id]');return {readonly:field.readOnly,value:field.value};})()"),
            { readonly: true, value: 'server-resource' });
        await run('window.RemoteVoiceManager.close();true;');
    } finally {
        delete state.overwriteMode; delete state.statusResult; delete state.beforeOverwriteResponse;
        state.voices[ref] = original;
        await run("window.RemoteVoiceManager.close();document.getElementById('voiceProvider').value='cosyvoice';document.getElementById('voiceProvider').dispatchEvent(new Event('change'));true;");
    }
}
async function verifyVoiceRaces({ run, waitFor, state }) {
    await run(`(() => {
        document.getElementById('voiceProvider').value = 'cosyvoice';
        document.getElementById('voiceProvider').dispatchEvent(new Event('change'));
        window.__raceRefreshes = 0;
        window.__raceLoadVoices = window.loadVoices;
        window.loadVoices = async (...args) => { await window.__raceLoadVoices(...args); window.__raceRefreshes++; };
        return true;
    })()`);
    const initialBinding = state.binding;
    try {
        let acknowledgements = 0;
        for (const action of ['search', 'refresh', 'manualEntry', 'backToList']) {
            const importGate = gate(); state.beforeImportResponse = importGate.hold;
            const initialImports = state.imports.length;
            try {
                await run("document.getElementById('importExistingVoice').click();true;");
                await waitFor("document.querySelector('.remote-voice-table tbody tr') && document.querySelector('.remote-voice-dialog').getAttribute('aria-busy')==='false'");
                await run(`(() => {
                    const click = key => Array.from(document.querySelectorAll('.remote-voice-dialog button')).find(button => button.textContent === window.t('voice.remote.' + key)).click();
                    if (${JSON.stringify(action)} === 'backToList') {
                        click('manualEntry');
                        const input = document.querySelector('input[name=remote_voice_id]');
                        input.value = 'ManualRace'; input.dispatchEvent(new Event('input')); click('import');
                    } else { Array.from(document.querySelectorAll('.remote-voice-table input[type=radio]')).find(input => !input.disabled).click(); click('importSelected'); }
                    return true;
                })()`);
                await bounded(importGate.started);
                assert.equal(state.imports.length, initialImports + 1);
                const actionResult = await run(`(() => {
                    const action = ${JSON.stringify(action)};
                    const control = action === 'search' ? document.querySelector('input[type=search]') :
                        Array.from(document.querySelectorAll('.remote-voice-dialog button')).find(button => button.textContent === window.t('voice.remote.' + action));
                    const disabled = control.disabled;
                    control.dispatchEvent(new Event(action === 'search' ? 'input' : 'click'));
                    return { disabled, busy: document.querySelector('.remote-voice-dialog').getAttribute('aria-busy') };
                })()`);
                assert.deepEqual(actionResult, { disabled: true, busy: 'true' });
                importGate.release();
                await waitFor(`document.querySelector('.remote-voice-status').textContent===window.t('voice.remote.imported') && window.__raceRefreshes===${++acknowledgements}`);
                assert.equal(state.imports.length, initialImports + 1);
                assert.equal(state.binding, initialBinding);
                await run("document.querySelector('.remote-voice-close').click();true;");
            } finally { importGate.release(); delete state.beforeImportResponse; }
        }
        const ref = Object.keys(state.voices).at(-1), voice = state.voices[ref];
        const previewGate = gate(); state.beforePreviewResponse = previewGate.hold;
        await run(`(() => {
            window.__raceAudio = [];
            window.__raceOriginalAudio = window.Audio;
            window.__raceOriginalConfirm = window.confirm;
            window.Audio = class {
                constructor(src) { this.src = src; this.played = false; this.paused = false; this.released = false; window.__raceAudio.push(this); }
                addEventListener() {}
                async play() { this.played = true; }
                pause() { this.paused = true; }
                removeAttribute() { this.src = ''; }
                load() { this.released = true; }
            };
            window.confirm = () => true;
            localStorage.removeItem('voice_preview_' + ${JSON.stringify(ref)});
            window.__racePreviewPending = playPreview(${JSON.stringify(ref)}, document.querySelector('[data-voice-id="${ref}"] .voice-preview-btn'), ${JSON.stringify(voice)});
            return true;
        })()`);
        try {
            await bounded(previewGate.started);
            await run(`deleteVoice(${JSON.stringify(ref)}, 'Controlled voice')`);
            assert.equal(state.voices[ref], undefined);
            previewGate.release();
            await run('window.__racePreviewPending');
            assert.deepEqual(await run(`({ audioCount: window.__raceAudio.length, cache: localStorage.getItem('voice_preview_' + ${JSON.stringify(ref)}), sessions: activeVoicePreviewSessions.size })`),
                { audioCount: 0, cache: null, sessions: 0 });
            delete state.beforePreviewResponse;
            const playingRef = Object.keys(state.voices).at(-1);
            await run(`playPreview(${JSON.stringify(playingRef)}, document.querySelector('[data-voice-id="${playingRef}"] .voice-preview-btn'), ${JSON.stringify(state.voices[playingRef])})`);
            assert.equal(await run('window.__raceAudio[0].played'), true);
            await run(`deleteVoice(${JSON.stringify(playingRef)}, 'Controlled voice')`);
            assert.deepEqual(await run('({ paused: window.__raceAudio[0].paused, released: window.__raceAudio[0].released, sessions: activeVoicePreviewSessions.size })'),
                { paused: true, released: true, sessions: 0 });
        } finally {
            previewGate.release(); delete state.beforePreviewResponse;
            await run('window.Audio=window.__raceOriginalAudio;window.confirm=window.__raceOriginalConfirm;true;');
        }
    } finally { await run('window.loadVoices=window.__raceLoadVoices;true;'); }
    await verifyOverwriteClaimConflict({ run, waitFor, state });
    await verifyOverwriteOutcomes({ run, waitFor, state });
    return { importAcknowledgementOwned: true, deletionRejectsLatePreview: true, playingAudioReleased: true,
        overwriteClaimConflictRecoverable: true, overwriteOutcomeActions: true,
        overwriteLateResponseFenced: true, serverResourceReadonly: true };
}
module.exports = { verifyVoiceRaces };
