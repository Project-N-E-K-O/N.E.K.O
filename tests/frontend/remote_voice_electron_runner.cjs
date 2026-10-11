'use strict';
// Keep the profile owner outside Electron so Windows has closed its file handles before removal.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createPageDiagnostics, removeTestProfile } = require('./remote_voice_page_diagnostics.cjs');
const diagnostics = createPageDiagnostics('electron-launcher');
const electron = require(process.env.NEKO_TEST_ELECTRON_MODULE || 'electron');
const script = process.argv[2];
if (!script) throw new Error('An Electron page test script is required');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-remote-voice-profile-'));
const environment = { ...process.env, NEKO_TEST_ELECTRON_PROFILE: profile };
delete environment.ELECTRON_RUN_AS_NODE;
const child = spawn(electron, [path.resolve(script)], { env: environment, stdio: 'inherit', windowsHide: true });
const watchdog = setTimeout(() => {
    console.error('Electron page test exceeded 90 seconds');
    child.kill('SIGKILL');
}, 90000);
let finished = false;
function finish(code, error) {
    if (finished) return;
    finished = true;
    clearTimeout(watchdog);
    if (error) console.error(error);
    try { removeTestProfile(profile); }
    catch (cleanupError) { console.error(cleanupError); code = 1; }
    diagnostics.finish({ exitCode: code, profileRemoved: !fs.existsSync(profile) },
        error || (code ? new Error('Electron page test exited unsuccessfully') : undefined));
    process.exitCode = code;
}
child.once('error', error => finish(1, error));
child.once('exit', (code, signal) => finish(code === null ? 1 : code,
    signal ? new Error('Electron terminated by ' + signal) : undefined));
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => child.kill(signal));
