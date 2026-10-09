'use strict';
const { chromium } = require(process.env.NEKO_TEST_PLAYWRIGHT_MODULE || 'playwright');
const { createVoicePreviewServer } = require('./voice_preview_server.cjs');
const { verifyPreviewBodyRaces } = require('./voice_preview_races.cjs');
const { createPageDiagnostics, observeBrowserConsoleErrors } = require('./remote_voice_page_diagnostics.cjs');
const path = require('node:path');

const controlled = createVoicePreviewServer();
const diagnostics = createPageDiagnostics('voice-preview-chromium');
let browser, page, finishing, result = {};
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
        if (cause && page) await bounded(page.screenshot({ path: path.join(diagnostics.directory, 'failure.png'), timeout: 2000 }), 2000, 'Failure screenshot')
            .catch(captureError => diagnostics.log('screenshot-error', captureError));
        const cleanup = await Promise.allSettled([
            bounded(Promise.resolve().then(() => { controlled.server.closeAllConnections(); return controlled.close(); }), 5000, 'HTTP cleanup')
            , bounded(Promise.resolve().then(() => browser && browser.close()), 5000, 'Browser cleanup')
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
        process.exitCode = failure ? 1 : 0;
        // A stuck driver is also retired when its control pipe closes on exit.
        if (cleanup.some(item => item.status === 'rejected')) process.exit(1);
    })();
    return finishing;
}
const watchdog = setTimeout(() => { void finish(new Error('VOICE_PREVIEW_CHROMIUM_TIMEOUT')); }, 60000);
process.on('unhandledRejection', error => { void finish(error); });
process.on('uncaughtException', error => { void finish(error); });
(async () => {
    await new Promise((resolve, reject) => { controlled.server.once('error', reject); controlled.server.listen(0, '127.0.0.1', resolve); });
    const channel = process.env.NEKO_TEST_BROWSER_CHANNEL;
    browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
    if (finishing) { await bounded(browser.close(), 5000, 'Late browser cleanup'); return; }
    page = await browser.newPage();
    page.on('pageerror', error => diagnostics.error(error));
    page.on('console', message => diagnostics.log(message.type(), message.text()));
    await observeBrowserConsoleErrors(page, diagnostics);
    await page.goto('http://127.0.0.1:' + controlled.server.address().port + '/voice_clone?lanlan_name=Test');
    await page.waitForFunction(() => typeof playPreview === 'function' && typeof window.t === 'function' && document.querySelector('[data-voice-id="preview-body"]'));
    const run = code => page.evaluate(code);
    const waitFor = code => page.waitForFunction(code);
    const scenarios = await verifyPreviewBodyRaces({ run, transport: controlled, page: true, diagnostics });
    diagnostics.assertClean();
    result = { browser: await browser.version(), actualProductTemplate: true, controlledHttpTransport: true, ...scenarios };
    console.log(JSON.stringify(result));
})().then(() => finish(), error => finish(error));
