'use strict';
const { createVoicePreviewServer } = require('./voice_preview_server.cjs');
const { verifyPreviewBodyRaces } = require('./voice_preview_races.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { app, BrowserWindow } = require('electron');
// The shared Node launcher removes this profile after Electron releases Windows locks.
const userData = process.env.NEKO_TEST_ELECTRON_PROFILE;
if (!userData) throw Error('Run voice_preview_electron.cjs with remote_voice_electron_runner.cjs');
const scratch = process.env.NEKO_TEST_ARTIFACT_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'neko-preview-electron-artifacts-'));
fs.mkdirSync(scratch, { recursive: true });
app.setPath('userData', userData);
// Keep the main process alive until HTTP/profile cleanup and explicit exit status.
app.on('window-all-closed', () => {});
const transport = createVoicePreviewServer();
let win;
const logs = [];
const watchdog = setTimeout(() => { console.error('VOICE_PREVIEW_ELECTRON_TIMEOUT'); transport.server.closeAllConnections(); app.exit(2); }, 60000);
app.whenReady().then(async () => {
    let exitCode = 0;
    try {
        await new Promise(resolve => transport.server.listen(0, '127.0.0.1', resolve));
        win = new BrowserWindow({ show: false, webPreferences: { contextIsolation: false, nodeIntegration: false, sandbox: false, backgroundThrottling: false } });
        win.webContents.on('console-message', event => logs.push({ level: event.level, message: event.message }));
        await win.loadURL('http://127.0.0.1:' + transport.server.address().port + '/voice_clone?lanlan_name=Test');
        const run = code => win.webContents.executeJavaScript(code, true);
        await run(`new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(Error('Product page did not initialize')), 10000);
            const check = () => { if (typeof playPreview === 'function' && typeof window.t === 'function' && document.querySelector('[data-voice-id="preview-body"]')) { clearTimeout(timer); resolve(true); } else requestAnimationFrame(check); }; check();
        })`);
        await run(`window.__previewPageErrors = []; addEventListener('error', event => window.__previewPageErrors.push(event.message)); addEventListener('unhandledrejection', event => window.__previewPageErrors.push(String(event.reason))); true`);
        const results = await verifyPreviewBodyRaces({ run, transport, page: true });
        assert.deepEqual(await run('window.__previewPageErrors'), []);
        const summary = { electron: process.versions.electron, actualProductTemplate: true, controlledHttpTransport: true, ...results };
        fs.writeFileSync(path.join(scratch, 'voice-preview-electron-summary.json'), JSON.stringify(summary, null, 2));
        console.log(JSON.stringify(summary));
    } catch (error) {
        exitCode = 1;
        fs.writeFileSync(path.join(scratch, 'voice-preview-electron-summary.json'), JSON.stringify({ success: false, error: error.stack || String(error) }, null, 2));
        console.error(error);
        if (win && !win.isDestroyed()) fs.writeFileSync(path.join(scratch, 'voice-preview-electron-failure.png'), (await win.webContents.capturePage()).toPNG());
    } finally {
        clearTimeout(watchdog);
        fs.writeFileSync(path.join(scratch, 'voice-preview-electron-console.json'), JSON.stringify(logs, null, 2));
        if (win && !win.isDestroyed()) win.destroy();
        await transport.close();
        app.exit(exitCode);
    }
}).catch(error => { console.error(error); app.exit(1); });
