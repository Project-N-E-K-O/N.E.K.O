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
const wav = Buffer.alloc(44 + samples * 2);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(4096 * Math.sin(2 * Math.PI * 220 * i / sampleRate)), 44 + i * 2);
const wavPath = path.join(scratch, 'controlled-tone.wav'); fs.writeFileSync(wavPath, wav);
app.commandLine.appendSwitch('use-fake-device-for-media-stream');
app.commandLine.appendSwitch('use-fake-ui-for-media-stream');
app.commandLine.appendSwitch('use-file-for-fake-audio-capture', wavPath);
let server, win;
const requests = [];
const watchdog = setTimeout(() => { console.error('VOICE_READINESS_ELECTRON_TIMEOUT'); app.exit(2); }, 40000);
function json(response, value) { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)); }
async function waitFor(expression) {
    return win.webContents.executeJavaScript(`new Promise((resolve,reject)=>{const condition=()=>(${expression});if(condition())return resolve(true);const observer=new MutationObserver(()=>{if(condition()){clearTimeout(timer);observer.disconnect();resolve(true);}});observer.observe(document.body,{subtree:true,attributes:true,childList:true,characterData:true});const timer=setTimeout(()=>{observer.disconnect();reject(new Error('UI condition timed out'));},12000);})`);
}
app.whenReady().then(async () => {
    server = http.createServer((request, response) => {
        const url = new URL(request.url, 'http://127.0.0.1');
        const entry = { path: url.pathname, method: request.method }; requests.push(entry);
        if (url.pathname === '/api/config/page_config') return json(response, { autostart_csrf_token: 'controlled-electron-test' });
        if (url.pathname === '/api/config/steam_language') return json(response, { ui_language: 'zh-CN' });
        if (url.pathname === '/api/voice-identity/status') return json(response, { has_profile: false, runtime_mode: 'enforce', enrollment_active: false, enrollment: null, effective_reason: 'no_profile' });
        if (url.pathname === '/api/voice-identity/resources') return json(response, { can_enroll: true, wake_enabled: false, resources: { campp: { state: 'ready' }, silero: { state: 'ready' }, noise_reduction: { state: 'ready' }, wake_model: { state: 'missing', reason: 'WAKE_WORD_MODEL_MISSING' }, wake_runtime: { state: 'missing', reason: 'WAKE_WORD_RUNTIME_MISSING' } } });
        if (url.pathname === '/api/voice-identity/audio/check/isolation') return json(response, { token: 'controlled-ticket', ttl_seconds: 60 });
        if (url.pathname === '/api/voice-identity/audio/check/isolation/release') return json(response, { released: true });
        if (url.pathname === '/api/voice-identity/audio/check') {
            entry.token = request.headers['x-voice-input-check']; let bytes = 0;
            request.on('data', chunk => { bytes += chunk.length; });
            return request.on('end', () => { entry.bytes = bytes; json(response, { accepted: true, audio_contract: { revision: 1, noise_reduction_enabled: false } }); });
        }
        if (url.pathname === '/api/voice-identity/enrollment/start') {
            let body=''; request.on('data',chunk=>{body+=chunk;});
            return request.on('end',()=>{entry.body=JSON.parse(body);response.writeHead(409,{'Content-Type':'application/json'});response.end(JSON.stringify({error_code:'audio_contract_changed'}));});
        }
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
    await win.webContents.executeJavaScript("localStorage.setItem('neko_selected_microphone','nonexistent-controlled-device');window.__controlledStreams=[];const gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.__controlledOriginal=gum;navigator.mediaDevices.getUserMedia=async options=>{const stream=await gum(options);window.__controlledStreams.push(stream);return stream;};document.getElementById('voice-identity-test').click();true;", true);
    await waitFor("!document.getElementById('voice-identity-test').disabled && document.getElementById('voice-identity-input-notice').textContent.length > 0");
    assert.equal(requests.some(r => r.path === '/api/voice-identity/audio/check'), false);
    await win.webContents.executeJavaScript("document.getElementById('voice-identity-test').click();true;", true);
    await waitFor("!document.getElementById('voice-identity-start').disabled");
    const check = requests.find(r => r.path === '/api/voice-identity/audio/check');
    assert.equal(check.token, 'controlled-ticket'); assert.equal(check.bytes, 288000);
    assert.equal(requests.some(r => /enrollment\/start|\/profile$/.test(r.path)), false);
    const ui = await win.webContents.executeJavaScript("({ actualDevice: document.getElementById('voice-identity-actual-device').textContent, meter: document.getElementById('voice-identity-meter').value, fallbackNotice: document.getElementById('voice-identity-input-notice').textContent, startEnabled: !document.getElementById('voice-identity-start').disabled })");
    assert.ok(ui.actualDevice); assert.notEqual(ui.actualDevice, '尚未启用麦克风'); assert.ok(ui.meter > 0);
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
    await win.webContents.executeJavaScript("const gain=document.getElementById('voice-identity-gain');gain.value='12';gain.dispatchEvent(new Event('change'));true;", true);
    assert.equal(await win.webContents.executeJavaScript("document.getElementById('voice-identity-start').disabled"), true);
    const report = { electron: process.versions.electron, actualPageAndWorklet: true, controlledApi: true, realMicrophoneCaptured: false, fallbackRequiresSecondTest: true, inputResourcesReleasedAfterTrial: true, repeatedTrialReacquiresStream: true, cancelledLatePermissionStopped: true, formalReopensAndChecksContract: true, changedServerContractRequiresRetest: true, pcmBytes: check.bytes, gainChangeInvalidatesTest: true, ui };
    fs.writeFileSync(path.join(scratch, 'result.json'), JSON.stringify(report, null, 2));
    console.log('VOICE_READINESS_ELECTRON ' + JSON.stringify(report));
    console.log('VOICE_READINESS_ARTIFACTS ' + scratch);
    clearTimeout(watchdog); win.destroy(); server.close(); app.exit(0);
}).catch(error => { console.error(error.stack); clearTimeout(watchdog); if (win) win.destroy(); if (server) server.close(); app.exit(1); });
