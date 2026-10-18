'use strict';
// Real Chromium getUserMedia and AudioWorklet with fake input, without a cloud
// provider. Changing the platform decision during permission tests the await
// boundary: constraints, resampled PCM and its wire header must stay aligned.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-mic-capture-electron-'));
app.setPath('userData', path.join(scratch, 'user-data'));
app.commandLine.appendSwitch('use-fake-device-for-media-stream');
app.commandLine.appendSwitch('use-fake-ui-for-media-stream');
let server, win;
const watchdog = setTimeout(() => { console.error('INTERCEPTION_CAPTURE_ELECTRON_TIMEOUT'); app.exit(2); }, 40000);
const html = `<!doctype html><html><body>
<script src="/static/app/app-state.js"></script>
<script src="/static/js/microphone-input.js"></script>
<script>
window.appUtils = { isMobile: () => window.captureMobile, dbToLinear: db => Math.pow(10, db / 20) };
window.t = key => key;
window.showStatusToast = () => {};
window.captureCalls = []; window.captureStreams = []; window.captureFrames = []; window.captureControls = [];
const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = async options => {
    window.captureCalls.push(options);
    const stream = await gum(options);
    window.captureStreams.push(stream);
    if (window.flipPlatformDuringPermission) window.captureMobile = !window.captureMobile;
    return stream;
};
window.appState.socket = { readyState: 1, send(data) {
    if (typeof data === "string") captureControls.push(JSON.parse(data));
    if (data instanceof ArrayBuffer) {
        const view = new DataView(data);
        window.captureFrames.push({ magic: String.fromCharCode(...new Uint8Array(data, 0, 4)),
            rate: view.getUint32(4, true), samples: (data.byteLength - 8) / 2 });
    }
} };
</script>
<script src="/static/app/app-audio-capture.js"></script>
</body></html>`;

async function runCaptureCase(origin, mobile, fallback = false, flip = false, normalEnd = true, host = "/") {
    await win.loadURL(origin + host);
    const result = await win.webContents.executeJavaScript(`(async () => {
        captureMobile = ${mobile}; flipPlatformDuringPermission = ${flip};
        appState.selectedMicrophoneId = ${fallback ? "'nonexistent-controlled-device'" : 'null'};
        setMicMuted(false);
        if (!await startMicCapture()) throw new Error('capture did not start');
        await new Promise((resolve, reject) => {
            const timer = setInterval(() => { if (captureFrames.length >= 3) {
                clearInterval(timer); clearTimeout(deadline); resolve();
            } }, 10);
            const deadline = setTimeout(() => { clearInterval(timer); reject(new Error('no real Worklet PCM')); }, 5000);
        });
        const result = { calls: captureCalls, frames: captureFrames.slice(0, 3),
            contextRate: appState.audioContext.sampleRate,
            effectiveAgc: captureStreams.at(-1).getAudioTracks()[0].getSettings().autoGainControl };
        await stopMicCapture({ normalEnd: ${normalEnd} });
        result.controls = captureControls;
        result.tracksReleased = captureStreams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'));
        return result;
    })()`, true);
    assert.equal(result.calls.length, fallback ? 2 : 1);
    assert.ok(result.calls.every(call => call.audio.autoGainControl === mobile));
    assert.equal(result.effectiveAgc, mobile);
    assert.equal(result.contextRate, 48000);
    assert.ok(result.frames.every(frame => frame.magic === 'NEKO' && frame.rate === (mobile ? 16000 : 48000) && frame.samples > 0));
    assert.equal(result.tracksReleased, true);
    const ends = result.controls.filter(item => item.event === "capture_end");
    assert.equal(ends.length, normalEnd ? 1 : 0);
    if (normalEnd) {
        const endIndex = result.controls.findIndex(item => item.event === "capture_end");
        const pauseIndex = result.controls.findIndex(item => item.action === "pause_session");
        assert.ok(endIndex >= 0 && pauseIndex > endIndex);
        assert.ok(result.controls.slice(0, endIndex).every(item => item.action !== "pause_session"));
    }
    if (fallback) {
        assert.equal(result.calls[0].audio.deviceId.exact, 'nonexistent-controlled-device');
        assert.equal(result.calls[1].audio.deviceId, undefined);
    }
    return { mobile, fallback, flip, normalEnd, host, ...result };
}

app.whenReady().then(async () => {
    server = http.createServer((request, response) => {
        const url = new URL(request.url, 'http://localhost');
        if (url.pathname === '/' || url.pathname === '/chat') {
            response.writeHead(200, { 'Content-Type': 'text/html' }); return response.end(html);
        }
        if (url.pathname.startsWith('/static/')) {
            const filename = path.resolve(root, '.' + decodeURIComponent(url.pathname));
            if (!filename.startsWith(path.join(root, 'static') + path.sep)) {
                response.writeHead(403); return response.end();
            }
            try {
                response.writeHead(200, { 'Content-Type': 'application/javascript' });
                return response.end(fs.readFileSync(filename));
            } catch (_) { response.writeHead(404); return response.end(); }
        }
        response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{}');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    win = new BrowserWindow({ show: false, skipTaskbar: true,
        webPreferences: { backgroundThrottling: false, nodeIntegration: false, contextIsolation: false } });
    win.webContents.session.setPermissionRequestHandler((_, __, callback) => callback(true));
    const cases = [];
    cases.push(await runCaptureCase(origin, false));
    cases.push(await runCaptureCase(origin, true));
    cases.push(await runCaptureCase(origin, true, true, true));
    cases.push(await runCaptureCase(origin, false, false, false, false));
    cases.push(await runCaptureCase(origin, false, false, false, true, "/chat"));
    cases.push(await runCaptureCase(origin, false, false, false, false, "/chat"));
    const report = { electron: process.versions.electron, actualCaptureAndWorklet: true,
        realMicrophoneCaptured: false, cases };
    fs.writeFileSync(path.join(scratch, 'result.json'), JSON.stringify(report, null, 2));
    console.log('INTERCEPTION_CAPTURE_ELECTRON ' + JSON.stringify(report));
    clearTimeout(watchdog); win.destroy(); server.close(); app.exit(0);
}).catch(error => {
    console.error(error.stack); clearTimeout(watchdog);
    if (win) win.destroy(); if (server) server.close(); app.exit(1);
});
