'use strict';
// Run with the desktop project's Electron binary. The test serves the actual
// enrollment assets with a controlled API and uses Chromium's fake input only.
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const os = require('node:os');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-voice-readiness-electron-'));
app.setPath('userData', path.join(scratch, 'user-data'));
const sampleRate = 48000, samples = sampleRate * 12;
const sentence = fs.readFileSync(path.join(root, 'tests/fixtures/voice_identity/issue_3347/rate4.wav'));
assert.equal(sentence.toString('ascii', 0, 4), 'RIFF');
assert.equal(sentence.readUInt32LE(24), sampleRate);
assert.equal(sentence.toString('ascii', 36, 40), 'data');
const wav = Buffer.alloc(44 + samples * 2);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
for (let i = 0; i < samples; i++) {
    const offset = i % (sampleRate * 3);
    wav.writeInt16LE(offset * 2 < sentence.length - 44 ? Math.round(sentence.readInt16LE(44 + offset * 2) * 0.25) : 0, 44 + i * 2);
}
const wavPath = path.join(scratch, 'controlled-quiet-sentence.wav'); fs.writeFileSync(wavPath, wav);
app.commandLine.appendSwitch('use-fake-device-for-media-stream');
app.commandLine.appendSwitch('use-fake-ui-for-media-stream');
app.commandLine.appendSwitch('use-file-for-fake-audio-capture', wavPath);
let server, win;
let allowEnrollment = false, enrollment = null;
const requests = [];
let hasProfile = false;
const watchdog = setTimeout(() => { console.error('VOICE_READINESS_ELECTRON_TIMEOUT'); app.exit(2); }, 40000);
function json(response, value) { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)); }
function status() {
    return { has_profile: hasProfile, profile_generation: hasProfile ? 'controlled-profile' : null, runtime_mode: 'enforce', enrollment_active: Boolean(enrollment), enrollment, effective_reason: hasProfile ? 'disabled' : 'no_profile' };
}
function measurePcm(pcm) {
    let activeSamples = 0;
    for (let offset = 0; offset + 960 <= pcm.length; offset += 960) {
        let sum = 0;
        for (let index = offset; index < offset + 960; index += 2) sum += (pcm.readInt16LE(index) / 32768) ** 2;
        if (Math.sqrt(sum / 480) >= 0.008) activeSamples += 480;
    }
    return activeSamples / sampleRate;
}
async function waitFor(expression) {
    return win.webContents.executeJavaScript(`new Promise((resolve,reject)=>{const condition=()=>(${expression});if(condition())return resolve(true);const observer=new MutationObserver(()=>{if(condition()){clearTimeout(timer);observer.disconnect();resolve(true);}});observer.observe(document.body,{subtree:true,attributes:true,childList:true,characterData:true});const timer=setTimeout(()=>{observer.disconnect();reject(new Error('UI condition timed out'));},12000);})`);
}
app.whenReady().then(async () => {
    server = http.createServer((request, response) => {
        const url = new URL(request.url, 'http://127.0.0.1');
        const entry = { path: url.pathname, method: request.method }; requests.push(entry);
        if (url.pathname === '/api/config/page_config') return json(response, { autostart_csrf_token: 'controlled-electron-test' });
        if (url.pathname === '/api/config/steam_language') return json(response, { ui_language: 'zh-CN' });
        if (url.pathname === '/api/voice-identity/status') return json(response, status());
        if (url.pathname === '/api/voice-identity/profile' && request.method === 'DELETE') {
            hasProfile = false;
            return json(response, status());
        }
        if (url.pathname === '/api/voice-identity/resources') return json(response, { can_enroll: true, wake_enabled: false, resources: { campp: { state: 'ready' }, silero: { state: 'ready' }, noise_reduction: { state: 'ready' }, wake_model: { state: 'missing', reason: 'WAKE_WORD_MODEL_MISSING' }, wake_runtime: { state: 'missing', reason: 'WAKE_WORD_RUNTIME_MISSING' } } });
        if (url.pathname === '/api/voice-identity/audio/check/isolation') return json(response, { token: 'controlled-ticket', ttl_seconds: 60 });
        if (url.pathname === '/api/voice-identity/audio/check/isolation/release') return json(response, { released: true });
        if (url.pathname === '/api/voice-identity/audio/check') {
            entry.token = request.headers['x-voice-input-check']; const chunks = [];
            request.on('data', chunk => { chunks.push(chunk); });
            return request.on('end', () => { const pcm = Buffer.concat(chunks); entry.bytes = pcm.length; entry.rmsActiveSeconds = measurePcm(pcm); pcm.fill(0); json(response, { accepted: true, audio_contract: { revision: 1, noise_reduction_enabled: false } }); });
        }
        if (url.pathname === '/api/voice-identity/enrollment/start') {
            let body=''; request.on('data',chunk=>{body+=chunk;});
            return request.on('end',()=>{
                entry.body=JSON.parse(body);
                if (!allowEnrollment) { response.writeHead(409,{'Content-Type':'application/json'}); return response.end(JSON.stringify({error_code:'audio_contract_changed'})); }
                enrollment = { enrollment_id: 'controlled-duration-session', profile_id: 'controlled-owner', next_segment_index: 1, accepted_segments: 0, required_segments: 4, phase: 'collecting_reference', remaining_seconds: 45 };
                json(response, status());
            });
        }
        if (url.pathname === '/api/voice-identity/enrollment/segment') {
            assert.equal(request.method, 'PUT');
            entry.segment = Number(request.headers['x-voice-identity-segment']);
            const chunks = [];
            request.on('data', chunk => chunks.push(chunk));
            return request.on('end', () => {
                const pcm = Buffer.concat(chunks);
                entry.bytes = pcm.length; entry.rmsActiveSeconds = measurePcm(pcm); pcm.fill(0);
                enrollment.next_segment_index = entry.segment + 1; enrollment.accepted_segments = entry.segment;
                json(response, status());
            });
        }
        if (url.pathname === '/api/voice-identity/enrollment/cancel') { enrollment = null; return json(response, status()); }
        if (url.pathname === '/voice_identity') { response.writeHead(200, { 'Content-Type': 'text/html;charset=utf-8' }); return response.end(fs.readFileSync(path.join(root, 'templates/voice_identity.html'), 'utf8').replaceAll('{{ static_asset_version }}', 'controlled')); }
        if (url.pathname.startsWith('/static/')) {
            const filename = path.resolve(root, '.' + decodeURIComponent(url.pathname));
            if (!filename.startsWith(path.join(root, 'static') + path.sep)) { response.writeHead(403); return response.end(); }
            try { response.writeHead(200, { 'Content-Type': { '.js': 'application/javascript', '.json': 'application/json', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(filename)] || 'application/octet-stream' }); return response.end(fs.readFileSync(filename)); } catch (_) {}
        }
        response.writeHead(404); response.end('{}');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    win = new BrowserWindow({ show: false, width: 960, height: 900, skipTaskbar: true, webPreferences: { contextIsolation: false, nodeIntegration: false, sandbox: false, backgroundThrottling: false } });
    win.webContents.session.setPermissionRequestHandler((_, __, callback) => callback(true));
    await win.loadURL(origin + '/voice_identity');
    await waitFor("!document.getElementById('voice-identity-test').disabled");
    assert.equal(await win.webContents.executeJavaScript("document.getElementById('voice-identity-start').disabled"), true);
    await waitFor("typeof window.t === 'function' && window.t('voiceIdentity.resourcesReady') !== 'voiceIdentity.resourcesReady' && document.getElementById('voice-identity-resource-summary').textContent === window.t('voiceIdentity.resourcesReady')");
    const setup = await win.webContents.executeJavaScript("({ hint: document.getElementById('voice-identity-start-hint').textContent, describedBy: document.getElementById('voice-identity-start').getAttribute('aria-describedby'), detailsOpen: document.querySelector('.resource-details').open, summaryVisible: document.getElementById('voice-identity-resource-summary').getBoundingClientRect().height > 0, downloadDisabled: document.getElementById('voice-identity-download').disabled, downloadHelpVisible: !document.getElementById('voice-identity-download-help').hidden, downloadHelp: document.getElementById('voice-identity-download-help').textContent })");
    assert.match(setup.hint, /请先完成试录/);
    assert.equal(setup.describedBy, 'voice-identity-start-hint');
    assert.equal(setup.detailsOpen, false);
    assert.equal(setup.summaryVisible, true);
    assert.equal(setup.downloadDisabled, true);
    assert.equal(setup.downloadHelpVisible, true);
    assert.match(setup.downloadHelp, /运行组件/);
    // Read Chromium's accessibility tree, including names contributed by CSS
    // pseudo-elements. Locale changes must keep one translated heading name.
    await waitFor("typeof window.changeLanguage === 'function'");
    const titleAccessibility = [];
    win.webContents.debugger.attach('1.3');
    try {
        await win.webContents.debugger.sendCommand('Accessibility.enable');
        for (const language of ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'pt', 'es']) {
            await win.webContents.executeJavaScript(`window.changeLanguage(${JSON.stringify(language)}).then(() => true)`);
            const expected = await win.webContents.executeJavaScript("window.t('voiceIdentity.pageTitle')");
            assert.notEqual(expected, 'voiceIdentity.pageTitle');
            const { root: documentNode } = await win.webContents.debugger.sendCommand('DOM.getDocument');
            const { nodeId } = await win.webContents.debugger.sendCommand('DOM.querySelector', { nodeId: documentNode.nodeId, selector: '.voice-identity-header h2' });
            const { nodes } = await win.webContents.debugger.sendCommand('Accessibility.getPartialAXTree', { nodeId, fetchRelatives: false });
            const heading = nodes.find(node => node.role?.value === 'heading');
            assert.ok(heading, `${language}: title is an accessible heading`);
            assert.equal(heading.name?.value, expected, `${language}: title must be announced once`);
            const titleText = await win.webContents.executeJavaScript("({ text: document.querySelector('.voice-identity-header h2').textContent, decoration: document.querySelector('.voice-identity-header h2').getAttribute('data-text') })");
            assert.equal(titleText.text, expected);
            assert.equal(titleText.decoration, expected);
            titleAccessibility.push({ language, name: heading.name.value });
        }
    } finally {
        win.webContents.debugger.detach();
    }
    await win.webContents.executeJavaScript("window.changeLanguage('zh-CN').then(() => true)");
    await win.webContents.executeJavaScript("localStorage.setItem('neko_selected_microphone','nonexistent-controlled-device');window.__controlledStreams=[];const gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.__controlledOriginal=gum;navigator.mediaDevices.getUserMedia=async options=>{const stream=await gum(options);window.__controlledStreams.push(stream);return stream;};document.getElementById('voice-identity-test').click();true;", true);
    await waitFor("!document.getElementById('voice-identity-test').disabled && document.getElementById('voice-identity-input-notice').textContent.length > 0");
    assert.equal(requests.some(r => r.path === '/api/voice-identity/audio/check'), false);
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-test').click();true;", true);
    await waitFor("!document.getElementById('voice-identity-start').disabled");
    const check = requests.find(r => r.path === '/api/voice-identity/audio/check');
    assert.equal(check.token, 'controlled-ticket'); assert.equal(check.bytes, 288000);
    assert.ok(check.rmsActiveSeconds < 1.5, 'Quiet real-worklet trial must bypass the old RMS duration gate');
    assert.equal(requests.some(r => /enrollment\/start|\/profile$/.test(r.path)), false);
    assert.match(await win.webContents.executeJavaScript("document.getElementById('voice-identity-start-hint').textContent"), /可以开始录入/);
    const ui = await win.webContents.executeJavaScript("({ actualDevice: document.getElementById('voice-identity-actual-device').textContent, meter: document.getElementById('voice-identity-meter').value, fallbackNotice: document.getElementById('voice-identity-input-notice').textContent, startEnabled: !document.getElementById('voice-identity-start').disabled })");
    assert.ok(ui.actualDevice); assert.notEqual(ui.actualDevice, '尚未启用麦克风');
    assert.ok(Number.isFinite(ui.meter) && ui.meter >= 0);
    assert.ok(check.rmsActiveSeconds > 0, 'The quiet sentence must contain signal, even though its trailing silence leaves the meter at zero');
    assert.equal(await win.webContents.executeJavaScript("window.__controlledStreams.every(stream=>stream.getAudioTracks().every(track=>track.readyState==='ended'))"), true);
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-test').click();true;", true);
    await waitFor("!document.getElementById('voice-identity-start').disabled");
    assert.equal(await win.webContents.executeJavaScript('window.__controlledStreams.length'), 3);
    assert.equal(requests.filter(r => r.path === '/api/voice-identity/audio/check').length, 2);
    // Cancel an actual permission/setup wait; resolve its getUserMedia result
    // after cancellation and prove the real enrollment wiring stops that stream.
    await win.webContents.executeJavaScript("const previous=navigator.mediaDevices.getUserMedia;navigator.mediaDevices.getUserMedia=async options=>{const stream=await previous(options);await new Promise(resolve=>{window.__releaseDelayedInput=resolve;});return stream;};document.getElementById('voice-identity-test').click();true;", true);
    await win.webContents.executeJavaScript("new Promise(resolve=>{const timer=setInterval(()=>{if(window.__releaseDelayedInput){clearInterval(timer);resolve();}},10);})");
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-test-cancel').click();window.__releaseDelayedInput();true;", true);
    await win.webContents.executeJavaScript("new Promise(resolve=>setTimeout(resolve,100))");
    assert.equal(await win.webContents.executeJavaScript("window.__controlledStreams.at(-1).getAudioTracks().every(track=>track.readyState==='ended')"),true);
    assert.equal(requests.filter(r=>r.path==='/api/voice-identity/audio/check').length,2);
    await win.webContents.executeJavaScript("delete window.__releaseDelayedInput;navigator.mediaDevices.getUserMedia=async options=>{const stream=await navigator.mediaDevices.__controlledOriginal(options);window.__controlledStreams.push(stream);return stream;};document.getElementById('voice-identity-test').click();true;",true);
    await waitFor("!document.getElementById('voice-identity-start').disabled");
    // Formal enrollment reacquires the tested device and sends only the checked
    // DSP contract. A changed server contract requires another input test.
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-start').click();true;",true);
    await waitFor("!document.getElementById('voice-identity-test').disabled && document.getElementById('voice-identity-start').disabled");
    const start=requests.find(r=>r.path==='/api/voice-identity/enrollment/start');
    assert.ok(start);assert.deepEqual(start.body.preview_audio_contract,{revision:1,noise_reduction_enabled:false});
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-test').click();true;",true);
    await waitFor("!document.getElementById('voice-identity-start').disabled");
    // The same quiet input also reaches formal upload after a manual finish.
    allowEnrollment = true;
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-start').click();true;", true);
    await waitFor("!document.getElementById('voice-identity-finish').hidden");
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-finish').click();true;", true);
    await waitFor("document.getElementById('voice-identity-message').textContent.includes('1.5')");
    assert.equal(requests.some(r => r.path === '/api/voice-identity/enrollment/segment'), false);
    await waitFor("parseFloat(document.getElementById('voice-identity-timer').textContent) >= 1.6");
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-finish').click();true;", true);
    await waitFor("!document.getElementById('voice-identity-next').hidden");
    const segment = requests.find(r => r.path === '/api/voice-identity/enrollment/segment');
    assert.equal(segment.segment, 1); assert.equal(segment.bytes, 288000);
    assert.ok(segment.rmsActiveSeconds > 0 && segment.rmsActiveSeconds < 1.5,
        'Formal upload must contain the quiet sentence, rather than only padding');
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-cancel').click();true;", true);
    await waitFor("!document.getElementById('voice-identity-test').disabled && document.getElementById('voice-identity-cancel').hidden");
    assert.equal(enrollment, null);
    assert.equal(await win.webContents.executeJavaScript("window.__controlledStreams.every(stream=>stream.getAudioTracks().every(track=>track.readyState==='ended'))"), true);
    await win.webContents.executeJavaScript("const gain=document.getElementById('voice-identity-gain');gain.value='12';gain.dispatchEvent(new Event('change'));true;", true);
    assert.equal(await win.webContents.executeJavaScript("document.getElementById('voice-identity-start').disabled"), true);
    assert.match(await win.webContents.executeJavaScript("document.getElementById('voice-identity-start-hint').textContent"), /请先完成试录/);
    hasProfile = true;
    await win.loadURL(origin + '/voice_identity');
    await waitFor("document.getElementById('voice-identity-enrollment').hidden && !document.getElementById('voice-identity-profile-controls').hidden && !document.getElementById('voice-identity-test').disabled");
    const profileLayout = await win.webContents.executeJavaScript("({ input: document.querySelector('.readiness-card').getBoundingClientRect().toJSON(), profile: document.querySelector('.profile-card').getBoundingClientRect().toJSON(), resources: document.querySelector('.resource-card').getBoundingClientRect().toJSON(), hint: document.getElementById('voice-identity-reenroll-hint').textContent })");
    assert.equal(profileLayout.profile.top, profileLayout.input.top);
    assert.ok(profileLayout.profile.left >= profileLayout.input.right);
    assert.ok(profileLayout.resources.top >= Math.max(profileLayout.profile.bottom, profileLayout.input.bottom));
    assert.match(profileLayout.hint, /请先完成试录/);
    win.setContentSize(390, 844);
    await win.webContents.executeJavaScript('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    const mobileProfile = await win.webContents.executeJavaScript("({ input: document.querySelector('.readiness-card').getBoundingClientRect().toJSON(), profile: document.querySelector('.profile-card').getBoundingClientRect().toJSON(), resources: document.querySelector('.resource-card').getBoundingClientRect().toJSON(), width: document.documentElement.scrollWidth, viewport: innerWidth })");
    assert.ok(mobileProfile.profile.top >= mobileProfile.input.bottom);
    assert.ok(mobileProfile.resources.top >= mobileProfile.profile.bottom);
    assert.equal(mobileProfile.width, mobileProfile.viewport);
    win.setContentSize(960, 900);
    const deletesBefore = requests.filter(r => r.path === '/api/voice-identity/profile' && r.method === 'DELETE').length;
    await win.webContents.executeJavaScript("window.showConfirm=()=>new Promise(resolve=>{window.__resolveProfileDelete=resolve;});document.getElementById('voice-identity-delete').click();true;");
    await waitFor("document.getElementById('voice-identity-delete').disabled && document.getElementById('voice-identity-profile-controls').hidden");
    assert.equal(hasProfile, true);
    assert.equal(requests.filter(r => r.path === '/api/voice-identity/profile' && r.method === 'DELETE').length, deletesBefore);
    await win.webContents.executeJavaScript("window.__resolveProfileDelete(false);delete window.__resolveProfileDelete;true;");
    await waitFor("!document.getElementById('voice-identity-delete').disabled && !document.getElementById('voice-identity-profile-controls').hidden");
    assert.equal(hasProfile, true);
    assert.equal(requests.filter(r => r.path === '/api/voice-identity/profile' && r.method === 'DELETE').length, deletesBefore);
    assert.equal(await win.webContents.executeJavaScript("document.querySelector('.profile-card').getBoundingClientRect().top === document.querySelector('.readiness-card').getBoundingClientRect().top"), true);
    await win.webContents.executeJavaScript("window.showConfirm=async()=>true;document.getElementById('voice-identity-delete').click();true;");
    await waitFor("!document.getElementById('voice-identity-enrollment').hidden && document.getElementById('voice-identity-profile-controls').hidden && !document.getElementById('voice-identity-test').disabled");
    assert.equal(hasProfile, false);
    assert.equal(requests.filter(r => r.path === '/api/voice-identity/profile' && r.method === 'DELETE').length, deletesBefore + 1);
    const deletedLayout = await win.webContents.executeJavaScript("({ input: document.querySelector('.readiness-card').getBoundingClientRect().toJSON(), enrollment: document.getElementById('voice-identity-enrollment').getBoundingClientRect().toJSON(), resources: document.querySelector('.resource-card').getBoundingClientRect().toJSON() })");
    assert.equal(deletedLayout.enrollment.top, deletedLayout.input.top);
    assert.ok(deletedLayout.enrollment.left >= deletedLayout.input.right);
    assert.ok(deletedLayout.resources.top >= Math.max(deletedLayout.enrollment.bottom, deletedLayout.input.bottom));
    const report = { electron: process.versions.electron, actualPageAndWorklet: true, controlledApi: true, realMicrophoneCaptured: false, realBackend: false, quietFixedSentenceUploaded: true, prematureManualFinishBlocked: true, quietManualFinishUploaded: true, cancelledFormalInputReleased: true, manualRmsActiveSeconds: segment.rmsActiveSeconds, trialRmsActiveSeconds: check.rmsActiveSeconds, titleAccessibility, fallbackRequiresSecondTest: true, inputResourcesReleasedAfterTrial: true, repeatedTrialReacquiresStream: true, cancelledLatePermissionStopped: true, formalReopensAndChecksContract: true, changedServerContractRequiresRetest: true, pcmBytes: check.bytes, gainChangeInvalidatesTest: true, cancelledDeletionPreservesProfile: true, profileDeletionRestoresLayoutWithoutReload: true, ui };
    fs.writeFileSync(path.join(scratch, 'result.json'), JSON.stringify(report, null, 2));
    console.log('VOICE_READINESS_ELECTRON ' + JSON.stringify(report));
    console.log('VOICE_READINESS_ARTIFACTS ' + scratch);
    clearTimeout(watchdog); win.destroy(); server.close(); app.exit(0);
}).catch(error => { console.error(error.stack); clearTimeout(watchdog); if (win) win.destroy(); if (server) server.close(); app.exit(1); });
