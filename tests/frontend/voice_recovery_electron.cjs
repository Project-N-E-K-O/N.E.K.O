'use strict';
const { app, BrowserWindow } = require('electron');
const { createRecoveryServer, verifyRecoveryPage } = require('./voice_recovery_races.cjs');
const { createPageDiagnostics } = require('./remote_voice_page_diagnostics.cjs');
const path = require('node:path');
const fs = require('node:fs');
if (!process.env.NEKO_TEST_ELECTRON_PROFILE) throw Error('Run through remote_voice_electron_runner.cjs');
app.setPath('userData', process.env.NEKO_TEST_ELECTRON_PROFILE);
app.on('window-all-closed', () => {});
const controlled = createRecoveryServer();
const diagnostics = createPageDiagnostics('voice-recovery-electron');
let win, finishing, result = {};
function bounded(operation, milliseconds, label) {
    let timer;
    return Promise.race([operation, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(label + ' timed out')), milliseconds);
    })]).finally(() => clearTimeout(timer));
}
function finish(error) {
    const explicitFailure = arguments.length > 0;
    const cause = explicitFailure ? (error instanceof Error ? error : new Error(String(error))) : undefined;
    // Record failures before the ownership guard, including a second error during cleanup.
    if (explicitFailure) diagnostics.error(cause);
    if (finishing) return finishing;
    finishing = (async () => {
        clearTimeout(watchdog);
        let failure = cause;
        if (cause && win && !win.isDestroyed()) {
            await bounded(win.webContents.capturePage(), 2000, 'Failure screenshot')
                .then(image => fs.writeFileSync(path.join(diagnostics.directory, 'failure.png'), image.toPNG()))
                .catch(captureError => diagnostics.log('screenshot-error', captureError));
        }
        try { if (win && !win.isDestroyed()) win.destroy(); }
        catch (cleanupError) { diagnostics.error(cleanupError); failure ||= cleanupError; }
        const cleanup = await Promise.allSettled([
            bounded(Promise.resolve().then(() => { controlled.server.closeAllConnections(); return controlled.close(); }), 5000, 'HTTP cleanup')
        ]);
        for (const item of cleanup) if (item.status === 'rejected') {
            const cleanupError = item.reason instanceof Error ? item.reason : new Error(String(item.reason));
            diagnostics.error(cleanupError); failure ||= cleanupError;
        }
        // Node reports unhandled rejections after this turn's promise callbacks.
        await new Promise(resolve => setImmediate(resolve));
        try { diagnostics.assertClean(); }
        catch (lateError) { failure ||= lateError; }
        diagnostics.finish(result, failure);
        if (failure) console.error(failure);
        app.exit(failure ? 1 : 0);
    })();
    return finishing;
}
const watchdog = setTimeout(() => { void finish(new Error('VOICE_RECOVERY_ELECTRON_TIMEOUT')); }, 60000);
process.on('unhandledRejection', error => { void finish(error); });
process.on('uncaughtException', error => { void finish(error); });
app.whenReady().then(async () => {
    await new Promise((resolve, reject) => { controlled.server.once('error', reject); controlled.server.listen(0, '127.0.0.1', resolve); });
    if (finishing) return;
    win = new BrowserWindow({ show: false, webPreferences: { contextIsolation: false, nodeIntegration: false, sandbox: false, backgroundThrottling: false } });
    win.webContents.on('console-message', event => diagnostics.log(event.level, event.message));
    win.webContents.on('render-process-gone', (_event, details) => diagnostics.error(new Error('Renderer process gone: ' + JSON.stringify(details))));
    win.webContents.debugger.attach('1.3');
    win.webContents.debugger.on('message', (_event, method, params) => {
        if (method === 'Runtime.exceptionThrown') {
            const detail = params.exceptionDetails;
            diagnostics.error(new Error(detail.exception?.description || detail.text));
        } else if (method === 'Runtime.consoleAPICalled' && params.type === 'error') {
            diagnostics.consoleError(params.args);
        }
    });
    // Runtime.enable waits for the first document in a hidden window; load concurrently.
    const runtimeCapture = win.webContents.debugger.sendCommand('Runtime.enable');
    await win.loadURL('http://127.0.0.1:' + controlled.server.address().port + '/voice_clone?lanlan_name=Test');
    await runtimeCapture;
    const run = code => win.webContents.executeJavaScript(code, true);
    const waitFor = code => run(`new Promise((resolve,reject) => {
        const guard = setTimeout(() => { observer.disconnect(); reject(Error('UI timeout: '+${JSON.stringify(code)})); }, 10000);
        const check = () => { if (${code}) { clearTimeout(guard); observer.disconnect(); resolve(true); } };
        const observer = new MutationObserver(check); observer.observe(document.body,{subtree:true,childList:true,attributes:true,characterData:true}); check();
    })`);
    await waitFor("typeof window.t === 'function' && typeof RemoteVoiceManager === 'object' && document.querySelector('[data-voice-id]')");
    const scenarios = await verifyRecoveryPage({ run, waitFor, controlled });
    diagnostics.assertClean();
    result = { electron: process.versions.electron, actualProductTemplate: true, controlledHttpTransport: true, ...scenarios };
    console.log(JSON.stringify(result));
}).then(() => finish(), error => finish(error));
